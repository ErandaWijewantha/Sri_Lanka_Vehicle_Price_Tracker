"""Postgres access: page upserts, price-change detection, delisting."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

from scraper import config
from scraper.models import Listing


def connect(url: str | None = None) -> psycopg.Connection:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    # prepare_threshold=None: Supabase's pooler (transaction mode) can't keep
    # server-side prepared statements across transactions.
    # autocommit=True: every write goes through an explicit conn.transaction() block.
    return psycopg.connect(url, prepare_threshold=None, autocommit=True)


@dataclass
class PageResult:
    new: int = 0
    updated: int = 0
    price_changes: int = 0
    reactivated: int = 0
    known_ids: set[str] = field(default_factory=set)   # ad_ids that existed before this page


_UPSERT = """
insert into listings (
  source, ad_id, url, title, category, vehicle_type, make, model, model_raw, year,
  condition, price, mileage_km, city, is_promoted, status, first_seen, last_seen,
  missed_sweeps, delisted_at
) values (
  %(source)s, %(ad_id)s, %(url)s, %(title)s, %(category)s, %(vehicle_type)s, %(make)s,
  %(model)s, %(model_raw)s, %(year)s, %(condition)s, %(price)s, %(mileage_km)s, %(city)s,
  %(is_promoted)s, 'active', %(now)s, %(now)s, 0, null
)
on conflict (source, ad_id) do update set
  url = excluded.url,
  title = excluded.title,
  category = excluded.category,
  vehicle_type = excluded.vehicle_type,
  make = excluded.make,
  model = excluded.model,
  model_raw = excluded.model_raw,
  year = coalesce(excluded.year, listings.year),
  condition = coalesce(excluded.condition, listings.condition),
  price = excluded.price,
  mileage_km = coalesce(excluded.mileage_km, listings.mileage_km),
  city = coalesce(excluded.city, listings.city),
  is_promoted = excluded.is_promoted,
  status = 'active',
  last_seen = excluded.last_seen,
  missed_sweeps = 0,
  delisted_at = null
"""

_INSERT_PRICE_CHANGE = """
insert into price_changes (source, ad_id, old_price, new_price, changed_at)
values (%s, %s, %s, %s, %s)
"""


def upsert_page(conn: psycopg.Connection, listings: list[Listing], now: datetime) -> PageResult:
    """Insert new ads, refresh known ones and log price changes, in one transaction."""
    result = PageResult()
    if not listings:
        return result
    source = listings[0].source
    ids = [l.ad_id for l in listings]

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "select ad_id, price, status from listings where source = %s and ad_id = any(%s)",
            (source, ids),
        )
        existing = {ad_id: (price, status) for ad_id, price, status in cur.fetchall()}
        result.known_ids = set(existing)

        changes: list[tuple[str, str, int | None, int | None, datetime]] = []
        for l in listings:
            if l.ad_id not in existing:
                result.new += 1
                continue
            result.updated += 1
            old_price, status = existing[l.ad_id]
            if status != "active":
                result.reactivated += 1
            if old_price != l.price:
                changes.append((source, l.ad_id, old_price, l.price, now))

        cur.executemany(_UPSERT, [{**vars(l), "now": now} for l in listings])
        if changes:
            cur.executemany(_INSERT_PRICE_CHANGE, changes)
        result.price_changes = len(changes)
    return result


@dataclass
class DelistResult:
    missed: int
    delisted: int


def finish_sweep(
    conn: psycopg.Connection,
    source: str,
    category: str,
    started_at: datetime,
    finished_at: datetime,
    pages: int,
    ads_seen: int,
) -> DelistResult:
    """Record a completed sweep; count a miss for every active ad it didn't see, delist repeat misses."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "insert into sweeps (source, category, started_at, finished_at, pages, ads_seen)"
            " values (%s, %s, %s, %s, %s, %s)",
            (source, category, started_at, finished_at, pages, ads_seen),
        )
        cur.execute(
            "update listings set missed_sweeps = missed_sweeps + 1"
            " where source = %s and category = %s and status = 'active' and last_seen < %s",
            (source, category, started_at),
        )
        missed = cur.rowcount
        cur.execute(
            "update listings set status = 'delisted', delisted_at = %s"
            " where source = %s and category = %s and status = 'active' and missed_sweeps >= %s",
            (finished_at, source, category, config.DELIST_AFTER_MISSED_SWEEPS),
        )
        delisted = cur.rowcount
    return DelistResult(missed=missed, delisted=delisted)


def apply_schema(conn: psycopg.Connection, path: str = "db/schema.sql") -> None:
    with open(path, encoding="utf-8") as f, conn.transaction():
        conn.execute(f.read())


@dataclass
class BackfillState:
    source: str
    category: str
    next_page: int = 1
    cycle_started_at: datetime | None = None
    pages_done: int = 0
    failed_pages: int = 0
    ads_seen: int = 0
    completed_at: datetime | None = None


_BACKFILL_COLS = "source, category, next_page, cycle_started_at, pages_done, failed_pages, ads_seen, completed_at"


def load_backfill_state(conn: psycopg.Connection, source: str, categories: list[str]) -> dict[str, BackfillState]:
    """Cursor per category, creating rows for categories seen for the first time."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "insert into backfill_state (source, category) values (%s, %s) on conflict do nothing",
            [(source, c) for c in categories],
        )
        cur.execute(
            f"select {_BACKFILL_COLS} from backfill_state where source = %s and category = any(%s)",
            (source, categories),
        )
        return {row[1]: BackfillState(*row) for row in cur.fetchall()}


def save_backfill_state(conn: psycopg.Connection, s: BackfillState) -> None:
    conn.execute(
        "update backfill_state set next_page = %s, cycle_started_at = %s, pages_done = %s,"
        " failed_pages = %s, ads_seen = %s, completed_at = %s, updated_at = now()"
        " where source = %s and category = %s",
        (s.next_page, s.cycle_started_at, s.pages_done, s.failed_pages, s.ads_seen, s.completed_at,
         s.source, s.category),
    )


def reset_backfill_cycle(conn: psycopg.Connection, source: str) -> None:
    """Start a new pass over every category of a source."""
    conn.execute(
        "update backfill_state set next_page = 1, cycle_started_at = null, pages_done = 0,"
        " failed_pages = 0, ads_seen = 0, completed_at = null, updated_at = now() where source = %s",
        (source,),
    )
