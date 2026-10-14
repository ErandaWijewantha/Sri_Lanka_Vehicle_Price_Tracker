from pathlib import Path

import pytest
from conftest import needs_fixtures

from scraper.config import RIYASEWANA_CATEGORIES
from scraper.sources import riyasewana

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = needs_fixtures
CARS = next(c for c in RIYASEWANA_CATEGORIES if c.slug == "cars")
BIKES = next(c for c in RIYASEWANA_CATEGORIES if c.slug == "motorcycles")


@pytest.fixture(scope="module")
def cars():
    return riyasewana.parse_listing_page((FIXTURES / "riyasewana_cars_p1.html").read_text("utf-8"), CARS)


def test_parses_every_card(cars):
    assert len(cars.listings) == 44            # 40 regular + 4 TOP ADs
    assert sum(l.is_promoted for l in cars.listings) == 4
    assert cars.total_ads == 24099
    assert len({l.ad_id for l in cars.listings}) == 44


def test_card_fields(cars):
    axio = next(l for l in cars.listings if l.ad_id == "12500688")
    assert axio.url == "https://riyasewana.com/buy/toyota-axio-sale-negombo-12500688"
    assert (axio.make, axio.model, axio.year) == ("Toyota", "axio", 2014)
    assert axio.price == 8_450_000
    assert axio.mileage_km == 101_000
    assert axio.city == "Negombo"
    assert axio.vehicle_type == "car"
    assert axio.category == "cars"
    assert axio.is_promoted is False


def test_promoted_card(cars):
    top = cars.listings[0]
    assert top.ad_id == "12474265"
    assert top.is_promoted is True
    assert top.model_raw == "Axio G Limited"
    assert top.city == "Dehiwala-Mt"


def test_negotiable_price_is_none(cars):
    assert next(l for l in cars.listings if l.ad_id == "12500695").price is None
    assert sum(l.price is None for l in cars.listings) == 9


def test_every_card_has_core_fields(cars):
    for l in cars.listings:
        assert l.make and l.model and l.year and l.city


def test_motorcycles_page():
    page = riyasewana.parse_listing_page(
        (FIXTURES / "riyasewana_motorcycles_p1.html").read_text("utf-8"), BIKES
    )
    assert len(page.listings) == 44
    assert {l.vehicle_type for l in page.listings} == {"motorcycle"}
    pulsar = next(l for l in page.listings if l.model_raw == "Pulsar NS200")
    assert (pulsar.make, pulsar.model) == ("Bajaj", "pulsar")


def test_empty_page_returns_no_listings():
    assert riyasewana.parse_listing_page("<html><body></body></html>", CARS).listings == []


def test_page_url():
    assert riyasewana.page_url(CARS, 1) == "https://riyasewana.com/search/cars"
    assert riyasewana.page_url(CARS, 3) == "https://riyasewana.com/search/cars?page=3"
