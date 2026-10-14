"""riyasewana.com listing-page parser (pure: html -> listings)."""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from scraper import normalize
from scraper.config import RIYASEWANA_BASE, Category
from scraper.models import Listing, ParsedPage

SOURCE = "riyasewana"

_AD_ID = re.compile(r"-(\d+)/?$")
_TOTAL = re.compile(r"of\s+([\d,]+)\s+Search Results", re.I)
# Trailing vehicle-type word(s) in card titles: 'Toyota Axio 2015 Car'.
_TYPE_SUFFIX = re.compile(
    r"\s+(Car|SUV|Jeep|Van|Motorbike|Motorcycle|Scooter|Lorry|Truck|Three Wheel|Three-Wheel|"
    r"Tuk|Pickup|Cab|Crew Cab|Double Cab|Heavy-Duty|Heavy Duty|Bus|Tractor|Bicycle|Other)$",
    re.I,
)


def page_url(category: Category, page: int) -> str:
    url = f"{RIYASEWANA_BASE}/search/{category.slug}"
    return url if page == 1 else f"{url}?page={page}"


def _text(node: Node | None) -> str | None:
    if node is None:
        return None
    text = node.text(deep=True, separator=" ", strip=True)
    return re.sub(r"\s+", " ", text) or None


def _parse_meta(meta: Node | None) -> tuple[str | None, str | None]:
    """'<svg/>Colombo · <svg/>112,000 km' -> ('Colombo', '112,000 km')."""
    city = mileage = None
    for part in (_text(meta) or "").split("·"):
        part = part.strip()
        if not part:
            continue
        if re.search(r"\bkm\b", part, re.I):
            mileage = part
        elif city is None:
            city = part
    return city, mileage


def _parse_card(card: Node, category: Category) -> Listing | None:
    link = card.css_first(".v-card-title a")
    if link is None:
        return None
    url = link.attributes.get("href") or ""
    m = _AD_ID.search(url)
    if not m:
        return None
    if url.startswith("//"):
        url = "https:" + url

    full_title = link.attributes.get("title") or _text(link) or ""
    year = normalize.parse_year(_text(card.css_first(".v-card-year")))
    name = _TYPE_SUFFIX.sub("", full_title.strip())
    if year is not None:
        # Drop the year from 'Toyota Axio G Limited 2015'.
        name = re.sub(rf"\s*\b{year}\b\s*", " ", name).strip()
    else:
        year, name = normalize.extract_year(name)
    make, model, model_raw = normalize.split_title(name)

    city, mileage = _parse_meta(card.css_first(".v-card-meta"))
    classes = (card.attributes.get("class") or "").split()
    promoted = "promoted" in classes or card.css_first(".top-ribbon") is not None

    return Listing(
        source=SOURCE,
        ad_id=m.group(1),
        url=url,
        title=full_title or None,
        category=category.slug,
        vehicle_type=category.vehicle_type,
        make=make,
        model=model,
        model_raw=model_raw,
        year=year,
        condition=None,  # not shown on riyasewana listing cards
        price=normalize.parse_price(_text(card.css_first(".v-card-price")), category.vehicle_type),
        mileage_km=normalize.parse_mileage(mileage),
        city=city,
        is_promoted=promoted,
    )


def parse_listing_page(html: str, category: Category) -> ParsedPage:
    tree = HTMLParser(html)
    listings: list[Listing] = []
    seen: set[str] = set()
    for card in tree.css("ul.v-list > li.v-card"):
        listing = _parse_card(card, category)
        if listing and listing.ad_id not in seen:
            seen.add(listing.ad_id)
            listings.append(listing)

    total = None
    header = tree.css_first(".results-header h2")
    if header is not None:
        m = _TOTAL.search(header.text())
        if m:
            total = int(m.group(1).replace(",", ""))
    return ParsedPage(listings=listings, total_ads=total)
