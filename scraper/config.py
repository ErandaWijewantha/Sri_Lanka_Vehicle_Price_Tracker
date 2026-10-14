"""Static configuration: sources, categories, politeness settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

# The GitHub workflows set SCRAPER_USER_AGENT to include the repo URL.
USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "vehicle-price-tracker/0.1 (personal price-tracking project)",
)

# Politeness: seconds between requests to the same site (uniform jitter in the range).
# riyasewana returned HTTP 429 after ~480 pages at 1–2 s (~35 pages/min), so it gets
# a slower pace: ~14 pages/min, a full sweep takes ~2.5 h.
DELAY_S: dict[str, tuple[float, float]] = {
    "riyasewana": (3.0, 5.0),
    "ikman": (1.5, 3.0),
}
DEFAULT_DELAY_S = (2.0, 4.0)
MAX_TRIES = 3
BACKOFF_BASE_S = 5.0
MAX_RETRY_AFTER_S = 300.0
TIMEOUT_S = 30.0

# Abort a category after this many pages in a row fail.
MAX_CONSECUTIVE_FAILED_PAGES = 3

# fresh mode safety net
FRESH_MAX_PAGES = 60

# Rolling backfill: pages per source per run (runs every 3 h -> ~2,000 pages/day,
# so one full pass over a site takes about a day). Each chunk re-reads a few
# pages before the saved cursor.
BACKFILL_PAGES_PER_RUN = 250
BACKFILL_OVERLAP_PAGES = 2

# A sweep that ends before this fraction of the expected pages is treated
# as incomplete, and no ads in that category are counted as missed.
SWEEP_COMPLETE_FRACTION = 0.9

# Ads missing from this many consecutive full sweeps are marked delisted.
DELIST_AFTER_MISSED_SWEEPS = 2

# daily_stats only keeps groups with at least this many active ads
# (keeps the table well inside Supabase's 500 MB free tier).
STATS_MIN_GROUP_SIZE = 3

# Asia/Colombo, UTC+05:30 with no DST: a fixed offset avoids depending on tz data.
UTC_OFFSET = "05:30"


@dataclass(frozen=True)
class Category:
    slug: str            # path segment used in the site's URL
    vehicle_type: str    # normalized vehicle type


RIYASEWANA_BASE = "https://riyasewana.com"
RIYASEWANA_PAGE_SIZE = 40
RIYASEWANA_CATEGORIES: tuple[Category, ...] = (
    Category("cars", "car"),
    Category("suvs", "suv"),
    Category("vans", "van"),
    Category("motorcycles", "motorcycle"),
    Category("lorries", "lorry"),
    Category("three-wheels", "three_wheel"),
    Category("pickups", "pickup"),
    Category("heavy-duties", "heavy_duty"),
)

IKMAN_BASE = "https://ikman.lk"
IKMAN_PAGE_SIZE = 25
IKMAN_CATEGORIES: tuple[Category, ...] = (
    Category("cars", "car"),
    Category("motorbikes-scooters", "motorcycle"),
    Category("three-wheelers", "three_wheel"),
    Category("vans", "van"),
    Category("lorries", "lorry"),
    Category("heavy-duty", "heavy_duty"),
    Category("tractors", "tractor"),
    Category("buses", "bus"),
)
