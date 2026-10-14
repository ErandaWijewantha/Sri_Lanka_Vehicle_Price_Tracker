from datetime import date

import pytest

from scraper import normalize


@pytest.mark.parametrize(
    "text, vtype, expected",
    [
        ("Rs. 8,450,000", "car", 8_450_000),
        ("Rs 7,690,000", "car", 7_690_000),
        ("Negotiable", "car", None),
        ("", "car", None),
        (None, "car", None),
        ("Rs. 5,000", "car", None),            # junk
        ("Rs 69,900", "motorcycle", 69_900),   # fine for a bike
        ("Rs 5,000", "motorcycle", None),
    ],
)
def test_parse_price(text, vtype, expected):
    assert normalize.parse_price(text, vtype) == expected


@pytest.mark.parametrize(
    "text, expected",
    [("101,000 km", 101_000), ("0 km", 0), ("9,999,999 km", None), (None, None), ("km", None)],
)
def test_parse_mileage(text, expected):
    assert normalize.parse_mileage(text) == expected


def test_parse_year_rejects_future_and_ancient():
    today = date(2026, 10, 15)
    assert normalize.parse_year("2027", today) == 2027
    assert normalize.parse_year("2028", today) is None
    assert normalize.parse_year("1890", today) is None
    assert normalize.parse_year("abc", today) is None


def test_extract_year_from_middle_of_title():
    assert normalize.extract_year("TVS Ntorq 125 BRAND NEW 2026 S", date(2026, 10, 15)) == (
        2026,
        "TVS Ntorq 125 BRAND NEW S",
    )
    assert normalize.extract_year("Toyota Axio", date(2026, 10, 15)) == (None, "Toyota Axio")


@pytest.mark.parametrize(
    "title, make, model",
    [
        ("Suzuki Wagon R FZ Premium", "Suzuki", "wagon r"),
        ("Suzuki Wagon R FX Safety", "Suzuki", "wagon r"),
        ("Mercedes Benz C220", "Mercedes-Benz", "c-class"),
        ("Mercedes-Benz CLA 200 AMG", "Mercedes-Benz", "cla"),
        ("Hero Honda Splendor", "Hero-Honda", "splendor"),
        ("Toyota WXB Axio Hybrid", "Toyota", "axio"),
        ("Toyota Land Cruiser Prado 150 Diesel", "Toyota", "land cruiser prado"),
        ("Toyota Land Cruiser 202 V8 ZX", "Toyota", "land cruiser"),
        ("Honda Fit Aria city 1st owner", "Honda", "fit"),
        ("Bajaj CT-100", "Bajaj", "ct100"),
        ("Yamaha FZ-S Version 2", "Yamaha", "fz"),
        ("Nissan FB14", "Nissan", "sunny"),
        ("Horwin SK3", "Horwin", "sk3"),          # unknown make falls back to first words
        ("ZOTYE Z100", "Zotye", "z100"),
    ],
)
def test_split_title(title, make, model):
    got_make, got_model, model_raw = normalize.split_title(title)
    assert (got_make, got_model) == (make, model)
    assert model_raw and model_raw in title


def test_normalize_condition():
    assert normalize.normalize_condition("Brand New") == "brand_new"
    assert normalize.normalize_condition("Reconditioned") == "reconditioned"
    assert normalize.normalize_condition("Hatchback") is None
