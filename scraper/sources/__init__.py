"""Per-site parsers. Each module exposes SOURCE, page_url() and parse_listing_page()."""

from __future__ import annotations

from types import ModuleType

from scraper.config import IKMAN_CATEGORIES, IKMAN_PAGE_SIZE, RIYASEWANA_CATEGORIES, RIYASEWANA_PAGE_SIZE, Category
from scraper.sources import ikman, riyasewana

SOURCES: dict[str, tuple[ModuleType, tuple[Category, ...], int]] = {
    "riyasewana": (riyasewana, RIYASEWANA_CATEGORIES, RIYASEWANA_PAGE_SIZE),
    "ikman": (ikman, IKMAN_CATEGORIES, IKMAN_PAGE_SIZE),
}
