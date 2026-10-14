"""Rebuild daily_stats for one day from the listings table."""

from __future__ import annotations

from datetime import date

import psycopg

from scraper import config

# Groups by (vehicle_type, make, model, year). Missing values become
# 'unknown' / 0 because they're part of the primary key. Day boundaries use
# Sri Lanka time (UTC+05:30). Only groups with enough active ads are kept, so the table
# stays small.
_REBUILD = """
with tagged as (
  select
    coalesce(vehicle_type, 'unknown') as vehicle_type,
    coalesce(make, 'unknown')         as make,
    coalesce(model, 'unknown')        as model,
    coalesce(year, 0)                 as year,
    status,
    price,
    (first_seen at time zone %(tz)s::interval)::date  as first_day,
    (delisted_at at time zone %(tz)s::interval)::date as delisted_day
  from listings
  where status = 'active'
     or (delisted_at at time zone %(tz)s::interval)::date = %(day)s
)
insert into daily_stats (
  day, vehicle_type, make, model, year, active_count, new_count, delisted_count,
  median_price, min_price, max_price
)
select
  %(day)s, vehicle_type, make, model, year,
  count(*) filter (where status = 'active'),
  count(*) filter (where first_day = %(day)s),
  count(*) filter (where delisted_day = %(day)s),
  (percentile_cont(0.5) within group (order by price) filter (where status = 'active'))::bigint,
  min(price) filter (where status = 'active'),
  max(price) filter (where status = 'active')
from tagged
group by vehicle_type, make, model, year
having count(*) filter (where status = 'active') >= %(min_group)s
on conflict (day, vehicle_type, make, model, year) do update set
  active_count   = excluded.active_count,
  new_count      = excluded.new_count,
  delisted_count = excluded.delisted_count,
  median_price   = excluded.median_price,
  min_price      = excluded.min_price,
  max_price      = excluded.max_price
"""


def rebuild_daily_stats(conn: psycopg.Connection, day: date) -> int:
    """Replace daily_stats rows for `day`; returns the number of groups written."""
    params = {"day": day, "tz": config.UTC_OFFSET, "min_group": config.STATS_MIN_GROUP_SIZE}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("delete from daily_stats where day = %s", (day,))
        cur.execute(_REBUILD, params)
        return cur.rowcount
