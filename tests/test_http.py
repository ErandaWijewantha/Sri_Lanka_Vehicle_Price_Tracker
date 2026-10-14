import httpx
import pytest
from conftest import needs_fixtures

from scraper import http, run
from scraper.config import RIYASEWANA_CATEGORIES
from scraper.http import FetchError, PoliteClient, RateLimited
from scraper.sources import riyasewana

CARS = next(c for c in RIYASEWANA_CATEGORIES if c.slug == "cars")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)


def _client(handler) -> PoliteClient:
    return PoliteClient(delay_s=(0, 0), transport=httpx.MockTransport(handler))


def test_persistent_429_raises_rate_limited():
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(429, headers={"Retry-After": "60"})

    with _client(handler) as c, pytest.raises(RateLimited):
        c.get("https://example.test/a")
    assert len(calls) == 3


def test_retries_then_succeeds():
    responses = iter([httpx.Response(503), httpx.Response(200, text="ok")])
    with _client(lambda r: next(responses)) as c:
        assert c.get("https://example.test/a") == "ok"


def test_404_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    with _client(handler) as c, pytest.raises(FetchError):
        c.get("https://example.test/a")
    assert calls == [1]


class _FailAfterFirstPage:
    def __init__(self, html: str, exc: Exception) -> None:
        self.html, self.exc, self.requests = html, exc, 0

    def get(self, url: str) -> str:
        self.requests += 1
        if "page=" not in url:
            return self.html
        raise self.exc


def _fixture_html() -> str:
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / "riyasewana_cars_p1.html").read_text("utf-8")


@needs_fixtures
def test_category_aborts_after_consecutive_failed_pages():
    client = _FailAfterFirstPage(_fixture_html(), FetchError("timeout"))
    with pytest.raises(FetchError):
        run.scrape_category(client, None, riyasewana, CARS, 40, "full", None)
    assert client.requests == 4   # page 1 ok, then 3 failures in a row


@needs_fixtures
def test_rate_limit_aborts_immediately():
    client = _FailAfterFirstPage(_fixture_html(), RateLimited("429"))
    with pytest.raises(RateLimited):
        run.scrape_category(client, None, riyasewana, CARS, 40, "full", None)
    assert client.requests == 2
