from pathlib import Path
from urllib.parse import urlparse

import pytest
from conftest import needs_fixtures

from scraper.config import IKMAN_CATEGORIES
from scraper.models import LayoutError
from scraper.sources import ikman

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = needs_fixtures
CARS = next(c for c in IKMAN_CATEGORIES if c.slug == "cars")
BIKES = next(c for c in IKMAN_CATEGORIES if c.slug == "motorbikes-scooters")


def _parse(name, category):
    return ikman.parse_listing_page((FIXTURES / name).read_text("utf-8"), category)


@pytest.fixture(scope="module")
def cars():
    return _parse("ikman_cars_p1.html", CARS)


def test_parses_every_ad(cars):
    assert len(cars.listings) == 26
    assert cars.total_ads == 11124
    assert sum(l.is_promoted for l in cars.listings) == 2


def test_ad_fields(cars):
    vitz = next(l for l in cars.listings if l.ad_id == "6ac9e339bed598567a7b8bda")
    assert vitz.url == "https://ikman.lk/en/ad/toyota-vitz-2011-for-sale-gampaha-90"
    assert (vitz.make, vitz.model, vitz.year) == ("Toyota", "vitz", 2011)
    assert vitz.price == 6_285_000
    assert vitz.mileage_km == 125_000
    assert vitz.condition == "used"
    assert vitz.city == "Gampaha"
    assert vitz.vehicle_type == "car"
    assert vitz.is_promoted is False


def test_suv_body_type_maps_to_suv(cars):
    prado = next(l for l in cars.listings if l.model == "land cruiser")
    assert prado.vehicle_type == "suv"
    assert prado.price == 54_500_000


def test_details_without_body_type():
    page = _parse("ikman_cars_p2.html", CARS)
    vezel = next(l for l in page.listings if l.title == "Honda Vezel 2024")
    assert vezel.mileage_km == 50_000
    assert vezel.condition == "reconditioned"


def test_year_in_middle_of_title():
    page = _parse("ikman_motorbikes_p1.html", BIKES)
    ntorq = next(l for l in page.listings if l.title.startswith("TVS Ntorq 125 BRAND NEW"))
    assert (ntorq.make, ntorq.model, ntorq.year, ntorq.condition) == ("TVS", "ntorq", 2026, "brand_new")
    assert {l.vehicle_type for l in page.listings} == {"motorcycle"}


def test_missing_state_raises_layout_error():
    with pytest.raises(LayoutError):
        ikman.parse_listing_page("<html><body>nothing here</body></html>", CARS)


def test_page_url_only_uses_page_param():
    assert ikman.page_url(CARS, 1) == "https://ikman.lk/en/ads/sri-lanka/cars"
    url = ikman.page_url(CARS, 7)
    assert urlparse(url).query == "page=7"
