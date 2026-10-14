"""CLI entry point.

    python -m scraper.run --mode sync                         # every 3h: fresh, then a backfill chunk
    python -m scraper.run --mode fresh                        # new ads only
    python -m scraper.run --mode backfill --backfill-pages 100
    python -m scraper.run --mode full --source riyasewana     # one-shot sweep (manual)
    python -m scraper.run --mode stats                        # rebuild daily_stats only
    python -m scraper.run --mode init-db                      # apply db/schema.sql
    python -m scraper.run --mode fresh --dry-run --max-pages 1  # parse only, no DB
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from types import ModuleType

import psycopg
from dotenv import load_dotenv

from scraper import config, db, stats
from scraper.config import Category
from scraper.http import FetchError, PoliteClient, RateLimited
from scraper.models import LayoutError
from scraper.sources import SOURCES

log = logging.getLogger("scraper")

# Sri Lanka has no DST, so a fixed offset is exact.
COLOMBO = timezone(timedelta(hours=5, minutes=30))


@dataclass
class CategoryStats:
    source: str
    category: str
    mode: str = ""
    start_page: int = 1
    next_page: int = 1          # first page not yet fetched when the walk stopped
    reached_end: bool = False   # walked past the category's last page
    pages: int = 0
    ads: int = 0
    new: int = 0
    updated: int = 0
    price_changes: int = 0
    reactivated: int = 0
    failed_pages: int = 0
    missed: int = 0
    delisted: int = 0
    stop_reason: str = ""
    error: str = ""


@dataclass
class Budget:
    """Pages a run may still fetch from one source (shared across categories)."""
    remaining: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expected_pages(total_ads: int | None, page_size: int) -> int | None:
    return math.ceil(total_ads / page_size) if total_ads else None


def scrape_category(
    client: PoliteClient,
    conn: psycopg.Connection | None,
    module: ModuleType,
    category: Category,
    page_size: int,
    mode: str,
    max_pages: int | None = None,
    seen_this_run: set[str] | None = None,
    start_page: int = 1,
    budget: Budget | None = None,
    st: CategoryStats | None = None,
) -> CategoryStats:
    """Walk one category newest-first from `start_page`.

    mode 'fresh' stops once a page holds only known ads; 'full' and 'backfill'
    walk to the end (or until `max_pages` / `budget` runs out). `st` is updated
    page by page, so a caller still has the progress if this raises.
    `seen_this_run` holds ad_ids already processed in this run for the source.
    """
    st = st or CategoryStats(source=module.SOURCE, category=category.slug)
    st.mode, st.start_page, st.next_page = mode, start_page, start_page
    seen = seen_this_run if seen_this_run is not None else set()
    started_at = _now()
    expected: int | None = None
    cap = max_pages or (config.FRESH_MAX_PAGES if mode == "fresh" else None)

    page = start_page - 1
    consecutive_failures = 0
    while True:
        page += 1
        st.next_page = page
        if cap is not None and page - start_page + 1 > cap:
            st.stop_reason = f"page cap {cap}"
            break
        # Allow a couple of pages past the expected end: ads posted during a long walk push the tail back.
        if expected is not None and page > expected + 2:
            st.stop_reason = "past last page"
            st.reached_end = True
            break
        if budget is not None:
            if budget.remaining <= 0:
                st.stop_reason = "page budget used up"
                break
            budget.remaining -= 1

        url = module.page_url(category, page)
        try:
            html = client.get(url)
        except RateLimited:
            raise
        except FetchError as exc:
            st.failed_pages += 1
            consecutive_failures += 1
            log.error("giving up on page: %s", exc)
            if page == 1 or consecutive_failures >= config.MAX_CONSECUTIVE_FAILED_PAGES:
                raise
            continue
        consecutive_failures = 0
        st.pages += 1

        parsed = module.parse_listing_page(html, category)
        expected = _expected_pages(parsed.total_ads, page_size) or expected
        listings = parsed.listings
        if not listings:
            if page == 1 or (expected is not None and page <= expected * config.SWEEP_COMPLETE_FRACTION):
                raise LayoutError(f"{url}: parsed 0 ads from a page that should have ads")
            st.stop_reason = "empty page (end of results)"
            st.reached_end = True
            st.next_page = page + 1
            break

        st.ads += len(listings)
        # ikman can show the same ad on two pages at once with different prices
        # (one copy stale). Only the first sighting in a run counts, so we
        # don't log a fake price cut and a fake rise every run.
        repeats = {l.ad_id for l in listings if l.ad_id in seen}
        fresh = [l for l in listings if l.ad_id not in seen]
        seen.update(l.ad_id for l in fresh)
        st.next_page = page + 1
        if conn is None:
            continue
        result = db.upsert_page(conn, fresh, _now())
        st.new += result.new
        st.updated += result.updated
        st.price_changes += result.price_changes
        st.reactivated += result.reactivated

        if mode == "fresh":
            # Promoted ads are pinned to the top regardless of age, so they don't count.
            regular = [l for l in listings if not l.is_promoted]
            known = result.known_ids | repeats
            if regular and all(l.ad_id in known for l in regular):
                st.stop_reason = f"caught up at page {page}"
                break

    if mode == "full" and conn is not None and start_page == 1 and st.reached_end:
        res = _finish_pass(conn, st.source, st.category, started_at, st.pages, st.failed_pages, st.ads, expected)
        if res:
            st.missed, st.delisted = res.missed, res.delisted
    return st


def _finish_pass(
    conn: psycopg.Connection,
    source: str,
    category: str,
    started_at: datetime,
    pages: int,
    failed_pages: int,
    ads: int,
    expected: int | None,
) -> db.DelistResult | None:
    """Count misses / delist only after a pass that really covered the category."""
    enough_pages = expected is None or pages >= expected * config.SWEEP_COMPLETE_FRACTION
    few_failures = failed_pages <= max(1, (expected or 0) // 100)
    if not (enough_pages and few_failures):
        log.warning(
            "%s/%s pass incomplete (%d pages ok, %d failed, ~%s expected): skipping delisting",
            source, category, pages, failed_pages, expected,
        )
        return None
    return db.finish_sweep(conn, source, category, started_at, _now(), pages, ads)


def backfill_source(
    client: PoliteClient,
    conn: psycopg.Connection,
    module: ModuleType,
    categories: list[Category],
    page_size: int,
    budget: Budget,
    seen: set[str],
    rows: list[CategoryStats],
) -> None:
    """Continue the rolling backfill of one source within `budget` pages.

    Categories are finished in config order; a source's cycle ends when every
    category has reached its last page, then the next cycle starts at page 1.
    Raises RateLimited / LayoutError / FetchError after saving the cursor.
    """
    slugs = [c.slug for c in categories]
    states = db.load_backfill_state(conn, module.SOURCE, slugs)
    if all(s.completed_at for s in states.values()):
        db.reset_backfill_cycle(conn, module.SOURCE)
        states = db.load_backfill_state(conn, module.SOURCE, slugs)
        log.info("%s: backfill cycle complete, starting a new one", module.SOURCE)

    for category in categories:
        s = states[category.slug]
        if s.completed_at:
            continue
        if budget.remaining <= 0:
            break
        # Re-read a few pages before the cursor: ads removed since the last run
        # shift the list back, and this keeps them from slipping past unseen.
        start = 1 if s.next_page <= 1 else max(1, s.next_page - config.BACKFILL_OVERLAP_PAGES)
        if start == 1 and s.cycle_started_at is None:
            s.cycle_started_at = _now()
        st = CategoryStats(source=module.SOURCE, category=category.slug)
        rows.append(st)
        log.info("%s/%s: backfill from page %d (%d pages left in budget)",
                 module.SOURCE, category.slug, start, budget.remaining)
        try:
            scrape_category(client, conn, module, category, page_size, "backfill",
                            seen_this_run=seen, start_page=start, budget=budget, st=st)
        finally:
            s.next_page = st.next_page
            s.pages_done += st.pages
            s.failed_pages += st.failed_pages
            s.ads_seen += st.ads
            if st.reached_end:
                s.completed_at = _now()
                expected = st.next_page - 1
                res = _finish_pass(conn, s.source, s.category, s.cycle_started_at or _now(),
                                   s.pages_done, s.failed_pages, s.ads_seen, expected)
                if res:
                    st.missed, st.delisted = res.missed, res.delisted
            db.save_backfill_state(conn, s)
            log.info("%s/%s backfill: %s", module.SOURCE, category.slug, json.dumps(asdict(st)))


def stats_day(now: datetime | None = None) -> date:
    """The day daily_stats describes: the nightly run starts at 02:00 Colombo
    time and reports on the day that just ended, so shift back 4 hours."""
    return ((now or _now()).astimezone(COLOMBO) - timedelta(hours=4)).date()


_SUMMARY_KEYS = ["pages", "ads", "new", "updated", "price_changes", "reactivated", "failed_pages", "missed", "delisted"]


def _write_summary(rows: list[CategoryStats], mode: str, elapsed: float, requests: int) -> None:
    total = {k: sum(getattr(r, k) for r in rows) for k in _SUMMARY_KEYS}
    log.info("RUN mode=%s elapsed=%.0fs requests=%d %s", mode, elapsed, requests, json.dumps(total))

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### Scrape `{mode}` — {elapsed / 60:.1f} min, {requests} requests", "",
             "| source | category | mode | pages | " + " | ".join(_SUMMARY_KEYS[1:]) + " | stop / error |",
             "|---|---|---|" + "---|" * len(_SUMMARY_KEYS) + "---|"]
    for r in rows:
        pages = f"{r.start_page}–{r.next_page - 1}" if r.pages else "0"
        lines.append(f"| {r.source} | {r.category} | {r.mode} | {pages} | "
                     + " | ".join(str(getattr(r, k)) for k in _SUMMARY_KEYS[1:])
                     + f" | {r.error or r.stop_reason} |")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _run_source(
    args: argparse.Namespace,
    conn: psycopg.Connection | None,
    source: str,
    rows: list[CategoryStats],
) -> tuple[bool, int]:
    """Run the requested mode(s) for one source. Returns (failed, requests)."""
    module, categories, page_size = SOURCES[source]
    selected = [c for c in categories if not args.category or c.slug in args.category]
    max_pages = args.max_pages or (2 if args.dry_run else None)
    seen: set[str] = set()
    failed = False

    with PoliteClient(config.DELAY_S.get(source, config.DEFAULT_DELAY_S)) as client:
        try:
            if args.mode in ("fresh", "full", "sync"):
                walk_mode = "full" if args.mode == "full" else "fresh"
                for category in selected:
                    st = CategoryStats(source=source, category=category.slug)
                    rows.append(st)
                    log.info("%s/%s: %s", source, category.slug, walk_mode)
                    try:
                        scrape_category(client, conn, module, category, page_size, walk_mode,
                                        max_pages, seen, st=st)
                    except (LayoutError, FetchError) as exc:
                        if isinstance(exc, RateLimited):
                            raise
                        failed = True
                        st.error = str(exc)
                        log.error("%s/%s FAILED: %s", source, category.slug, exc)
                    log.info("%s/%s done: %s", source, category.slug, json.dumps(asdict(st)))

            if args.mode in ("backfill", "sync") and conn is not None:
                budget = Budget(args.backfill_pages)
                try:
                    backfill_source(client, conn, module, selected, page_size, budget, seen, rows)
                except (LayoutError, FetchError) as exc:
                    if isinstance(exc, RateLimited):
                        raise
                    failed = True
                    rows[-1].error = str(exc)
                    log.error("%s backfill FAILED: %s", source, exc)
        except RateLimited as exc:
            # Back off from the whole site for the rest of this run.
            failed = True
            if rows:
                rows[-1].error = str(exc)
            log.error("%s is rate limiting us; stopping this source for the run: %s", source, exc)
        return failed, client.requests


def run_scrape(args: argparse.Namespace) -> int:
    conn = None if args.dry_run else db.connect()
    sources = list(SOURCES) if args.source == "all" else [args.source]
    rows: list[CategoryStats] = []
    failed = False
    t0 = time.monotonic()
    requests = 0

    for source in sources:
        source_failed, n = _run_source(args, conn, source, rows)
        failed = failed or source_failed
        requests += n

    if args.mode == "full" and conn is not None:
        day = stats_day()
        n = stats.rebuild_daily_stats(conn, day)
        log.info("daily_stats rebuilt for %s: %d groups", day, n)

    _write_summary(rows, args.mode, time.monotonic() - t0, requests)
    if conn is not None:
        conn.close()
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m scraper.run")
    parser.add_argument("--mode", required=True,
                        choices=["sync", "fresh", "backfill", "full", "stats", "init-db"])
    parser.add_argument("--source", default="all", choices=["all", *SOURCES])
    parser.add_argument("--category", action="append", help="limit to a category slug (repeatable)")
    parser.add_argument("--max-pages", type=int, help="override the per-category page cap")
    parser.add_argument("--backfill-pages", type=int, default=config.BACKFILL_PAGES_PER_RUN,
                        help="backfill/sync: pages per source per run")
    parser.add_argument("--dry-run", action="store_true", help="fetch and parse only; no database")
    parser.add_argument("--day", type=date.fromisoformat, help="stats mode: day to rebuild (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )

    if args.mode == "init-db":
        with db.connect() as conn:
            db.apply_schema(conn)
        log.info("schema applied")
        return 0
    if args.mode == "stats":
        day = args.day or stats_day()
        with db.connect() as conn:
            n = stats.rebuild_daily_stats(conn, day)
        log.info("daily_stats rebuilt for %s: %d groups", day, n)
        return 0
    return run_scrape(args)


if __name__ == "__main__":
    sys.exit(main())
