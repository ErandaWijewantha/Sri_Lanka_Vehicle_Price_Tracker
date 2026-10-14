"""Normalized listing shape shared by all sources."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    source: str                 # 'riyasewana' | 'ikman'
    ad_id: str
    url: str
    title: str | None
    category: str               # site category slug the ad was found under
    vehicle_type: str
    make: str | None
    model: str | None
    model_raw: str | None
    year: int | None
    condition: str | None
    price: int | None           # LKR; None when negotiable / junk
    mileage_km: int | None
    city: str | None
    is_promoted: bool = False


@dataclass
class ParsedPage:
    listings: list[Listing] = field(default_factory=list)
    total_ads: int | None = None    # total results in the category, if the page says


class LayoutError(RuntimeError):
    """A page no longer looks like what the parser expects (site layout changed)."""
