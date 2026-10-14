"""Database integration tests. Run only when TEST_DATABASE_URL points at a
throwaway Postgres database (its tables are dropped and recreated)."""

import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import needs_fixtures

from scraper import db, run, stats
from scraper.config import RIYASEWANA_CATEGORIES
from scraper.sources import riyasewana

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = [pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set"), needs_fixtures]

FIXTURES = Path(__file__).parent / "fixtures"
CARS = next(c for c in RIYASEWANA_CATEGORIES if c.slug == "cars")
T0 = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = db.connect(URL)
    c.execute("drop table if exists price_changes, daily_stats, sweeps, backfill_state, listings cascade")
    db.apply_schema(c)
    db.apply_schema(c)  # idempotent
    yield c
    c.close()


@pytest.fixture(scope="module")
def page():
    return riyasewana.parse_listing_page((FIXTURES / "riyasewana_cars_p1.html").read_text("utf-8"), CARS)


def _count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_insert_then_update_and_price_change(conn, page):
    first = db.upsert_page(conn, page.listings, T0)
    assert (first.new, first.updated, first.price_changes) == (44, 0, 0)
    assert first.known_ids == set()

    changed = [replace(l, price=l.price - 100_000) if l.ad_id == "12500688" else l for l in page.listings]
    second = db.upsert_page(conn, changed, T0 + timedelta(hours=3))
    assert (second.new, second.updated, second.price_changes) == (0, 44, 1)
    assert len(second.known_ids) == 44

    row = conn.execute(
        "select old_price, new_price from price_changes where ad_id = '12500688'"
    ).fetchone()
    assert row == (8_450_000, 8_350_000)
    price, first_seen, last_seen = conn.execute(
        "select price, first_seen, last_seen from listings where ad_id = '12500688'"
    ).fetchone()
    assert price == 8_350_000
    assert first_seen == T0 and last_seen == T0 + timedelta(hours=3)


def test_delist_after_two_missed_sweeps_and_reactivate(conn, page):
    db.upsert_page(conn, page.listings, T0)
    seen_again = page.listings[:40]   # last 4 ads disappear
    gone = {l.ad_id for l in page.listings[40:]}

    for i in (1, 2):
        start = T0 + timedelta(days=i)
        db.upsert_page(conn, seen_again, start + timedelta(minutes=5))
        res = db.finish_sweep(conn, "riyasewana", "cars", start, start + timedelta(hours=1), 1, 40)
        assert res.missed == 4
        assert res.delisted == (0 if i == 1 else 4)

    delisted = {r[0] for r in conn.execute("select ad_id from listings where status = 'delisted'")}
    assert delisted == gone

    back = db.upsert_page(conn, page.listings[40:], T0 + timedelta(days=3))
    assert back.reactivated == 4
    assert _count(conn, "select count(*) from listings where status = 'active'") == 44
    assert _count(conn, "select max(missed_sweeps) from listings") == 0


def test_daily_stats(conn, page, monkeypatch):
    monkeypatch.setattr("scraper.config.STATS_MIN_GROUP_SIZE", 1)
    db.upsert_page(conn, page.listings, T0)
    n = stats.rebuild_daily_stats(conn, date(2026, 10, 14))
    assert n > 0
    # Two 2007 Axios, both 'Negotiable'.
    assert conn.execute(
        "select active_count, new_count, median_price from daily_stats"
        " where make = 'Toyota' and model = 'axio' and year = 2007"
    ).fetchone() == (2, 2, None)
    # Two 2018 Wagon Rs: Rs 6,675,000 and Rs 6,650,000.
    assert conn.execute(
        "select active_count, median_price, min_price, max_price from daily_stats"
        " where model = 'wagon r' and year = 2018"
    ).fetchone() == (2, 6_662_500, 6_650_000, 6_675_000)
    # Rebuilding the same day replaces rows instead of failing on the primary key.
    assert stats.rebuild_daily_stats(conn, date(2026, 10, 14)) == n


def test_daily_stats_min_group_size(conn, page):
    db.upsert_page(conn, page.listings, T0)
    # No (make, model, year) on this one page has 3+ ads.
    assert stats.rebuild_daily_stats(conn, date(2026, 10, 14)) == 0


class FakeClient:
    """Serves the fixture for page 1 and an empty results page after that."""

    def __init__(self, html: str) -> None:
        # Pretend the category has 44 ads, so page 2 is the legitimate end.
        self.html = html.replace("of 24099 Search Results", "of 44 Search Results")
        self.requests = 0

    def get(self, url: str) -> str:
        self.requests += 1
        return self.html if "page=" not in url else self.html.replace('class="v-card', 'class="gone')


def test_fresh_mode_stops_once_caught_up(conn):
    html = (FIXTURES / "riyasewana_cars_p1.html").read_text("utf-8")

    st = run.scrape_category(FakeClient(html), conn, riyasewana, CARS, 40, "fresh", None)
    assert st.new == 44
    assert st.stop_reason == "empty page (end of results)"

    client = FakeClient(html)
    st = run.scrape_category(client, conn, riyasewana, CARS, 40, "fresh", None)
    assert (st.new, st.updated) == (0, 44)
    assert st.stop_reason == "caught up at page 1"
    assert client.requests == 1


class RepeatingClient:
    """Page 2 repeats page 1's ads, one with a different (stale) price; page 3 is empty."""

    def __init__(self, html: str) -> None:
        html = html.replace("of 24099 Search Results", "of 88 Search Results")
        self.pages = {
            1: html,
            2: html.replace("Rs. 8,450,000", "Rs. 4,450,000"),
            3: html.replace('class="v-card', 'class="gone'),
        }

    def get(self, url: str) -> str:
        page = int(url.split("page=")[1]) if "page=" in url else 1
        return self.pages[page]


def test_repeat_sighting_in_one_run_is_not_a_price_change(conn):
    html = (FIXTURES / "riyasewana_cars_p1.html").read_text("utf-8")
    st = run.scrape_category(RepeatingClient(html), conn, riyasewana, CARS, 40, "full", None)
    assert (st.new, st.updated, st.price_changes) == (44, 0, 0)
    assert _count(conn, "select price from listings where ad_id = '12500688'") == 8_450_000


SUVS = next(c for c in RIYASEWANA_CATEGORIES if c.slug == "suvs")


class PagedClient:
    """A category with 5 pages (the fixture repeated); page 6+ is empty.
    Raises `fail_on` (page -> exception) when set."""

    def __init__(self, fail_on: dict[int, Exception] | None = None) -> None:
        html = (FIXTURES / "riyasewana_cars_p1.html").read_text("utf-8")
        self.html = html.replace("of 24099 Search Results", "of 200 Search Results")
        self.empty = self.html.replace('class="v-card', 'class="gone')
        self.fail_on = fail_on or {}
        self.pages: list[tuple[str, int]] = []
        self.requests = 0

    def get(self, url: str) -> str:
        page = int(url.split("page=")[1]) if "page=" in url else 1
        cat = url.split("/search/")[1].split("?")[0]
        self.requests += 1
        self.pages.append((cat, page))
        if page in self.fail_on:
            raise self.fail_on[page]
        return self.html if page <= 5 else self.empty


def _backfill(conn, client, budget):
    rows = []
    run.backfill_source(client, conn, riyasewana, [CARS, SUVS], 40, run.Budget(budget), set(), rows)
    return {s.category: s for s in db.load_backfill_state(conn, "riyasewana", ["cars", "suvs"]).values()}


def test_backfill_resumes_and_cycles(conn):
    c1 = PagedClient()
    state = _backfill(conn, c1, 4)
    assert c1.pages == [("cars", p) for p in (1, 2, 3, 4)]
    assert state["cars"].next_page == 5 and state["cars"].completed_at is None
    assert state["suvs"].next_page == 1

    # Resumes 2 pages before the cursor, reaches the end of cars, then budget runs out.
    c2 = PagedClient()
    state = _backfill(conn, c2, 4)
    assert c2.pages == [("cars", p) for p in (3, 4, 5, 6)]
    assert state["cars"].completed_at is not None
    assert _count(conn, "select count(*) from sweeps where category = 'cars'") == 1

    # Next run skips the finished category.
    c3 = PagedClient()
    state = _backfill(conn, c3, 10)
    assert c3.pages == [("suvs", p) for p in range(1, 7)]
    assert all(s.completed_at for s in state.values())

    # Everything finished -> a new cycle starts from page 1 of the first category.
    c4 = PagedClient()
    state = _backfill(conn, c4, 1)
    assert c4.pages == [("cars", 1)]
    assert state["suvs"].completed_at is None and state["suvs"].next_page == 1


def test_backfill_saves_cursor_when_rate_limited(conn):
    from scraper.http import RateLimited

    with pytest.raises(RateLimited):
        _backfill(conn, PagedClient(fail_on={3: RateLimited("429")}), 10)
    state = {s.category: s for s in db.load_backfill_state(conn, "riyasewana", ["cars", "suvs"]).values()}
    assert state["cars"].next_page == 3
    assert state["cars"].pages_done == 2
    assert state["cars"].cycle_started_at is not None
