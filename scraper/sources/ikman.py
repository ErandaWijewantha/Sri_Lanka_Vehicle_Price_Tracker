"""ikman.lk listing-page parser (pure: html -> listings).

ikman embeds the full search result as JSON in `window.initialData = {...}`;
we read that instead of the rendered HTML.
"""

from __future__ import annotations

import json
import re
from typing import Any

from scraper import normalize
from scraper.config import IKMAN_BASE, Category
from scraper.models import LayoutError, Listing, ParsedPage

SOURCE = "ikman"

_STATE_MARKER = re.compile(r"window\.initialData\s*=\s*")

# Body types on ikman's Cars category that map to another vehicle type.
_BODY_TYPE_OVERRIDES: dict[str, str] = {
    "suv / 4x4": "suv",
    "pickup": "pickup",
    "pick up": "pickup",
}


def page_url(category: Category, page: int) -> str:
    # robots.txt: only ?page= is allowed on ikman listing URLs.
    url = f"{IKMAN_BASE}/en/ads/sri-lanka/{category.slug}"
    return url if page == 1 else f"{url}?page={page}"


def _initial_data(html: str) -> dict[str, Any]:
    m = _STATE_MARKER.search(html)
    if not m:
        raise LayoutError("ikman: window.initialData not found")
    data, _ = json.JSONDecoder().raw_decode(html, m.end())
    return data


def _split_details(details: str) -> tuple[str | None, str | None, str | None]:
    """'125,000 km | Hatchback | Used' -> ('125,000 km', 'Hatchback', 'Used').

    Body type is missing on some ads and on non-car categories.
    """
    mileage = body = condition = None
    for part in (p.strip() for p in details.split("|")):
        if not part:
            continue
        if re.fullmatch(r"[\d,]+\s*km", part, re.I):
            mileage = part
        elif normalize.normalize_condition(part):
            condition = part
        elif body is None:
            body = part
    return mileage, body, condition


def _parse_ad(ad: dict[str, Any], category: Category) -> Listing | None:
    ad_id = ad.get("id")
    slug = ad.get("slug")
    if not ad_id or not slug:
        return None
    title = (ad.get("title") or "").strip()
    year, name = normalize.extract_year(title)
    make, model, model_raw = normalize.split_title(name)

    mileage, body, condition = _split_details(ad.get("details") or "")
    vehicle_type = category.vehicle_type
    if category.slug == "cars" and body:
        vehicle_type = _BODY_TYPE_OVERRIDES.get(body.lower(), vehicle_type)

    return Listing(
        source=SOURCE,
        ad_id=str(ad_id),
        url=f"{IKMAN_BASE}/en/ad/{slug}",
        title=title or None,
        category=category.slug,
        vehicle_type=vehicle_type,
        make=make,
        model=model,
        model_raw=model_raw,
        year=year,
        condition=normalize.normalize_condition(condition),
        price=normalize.parse_price(ad.get("price"), vehicle_type),
        mileage_km=normalize.parse_mileage(mileage),
        city=(ad.get("location") or "").strip() or None,
        is_promoted=bool(ad.get("isTopAd") or ad.get("isFeaturedAd")),
    )


def parse_listing_page(html: str, category: Category) -> ParsedPage:
    data = _initial_data(html)
    try:
        result = data["serp"]["ads"]["data"]
    except (KeyError, TypeError) as exc:
        raise LayoutError("ikman: serp.ads.data missing from initialData") from exc

    # `topAds` repeats ads that also appear in `ads`, so only `ads` is read.
    listings: list[Listing] = []
    seen: set[str] = set()
    for ad in result.get("ads") or []:
        if ad.get("adType") not in (None, "for_sale"):
            continue
        listing = _parse_ad(ad, category)
        if listing and listing.ad_id not in seen:
            seen.add(listing.ad_id)
            listings.append(listing)

    total = (result.get("paginationData") or {}).get("total")
    return ParsedPage(listings=listings, total_ads=int(total) if total is not None else None)
