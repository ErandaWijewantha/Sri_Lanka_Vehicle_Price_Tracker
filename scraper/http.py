"""Polite HTTP client: one request at a time, 1–2 s delay + jitter, retries with backoff."""

from __future__ import annotations

import logging
import random
import time

import httpx

from scraper import config

log = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """A page could not be fetched after all retries."""


class RateLimited(FetchError):
    """The site kept answering 429. Stop scraping it for this run."""


class PoliteClient:
    def __init__(
        self,
        delay_s: tuple[float, float] = config.DEFAULT_DELAY_S,
        user_agent: str = config.USER_AGENT,
        transport: httpx.BaseTransport | None = None,   # tests inject a mock
    ) -> None:
        self._delay_s = delay_s
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "en"},
            timeout=config.TIMEOUT_S,
            follow_redirects=True,
            transport=transport,
        )
        self._last_request = 0.0
        self.requests = 0

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def _wait_turn(self) -> None:
        delay = random.uniform(*self._delay_s)
        remaining = self._last_request + delay - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def get(self, url: str) -> str:
        """Fetch a URL; raise FetchError after MAX_TRIES failed attempts."""
        last_error = ""
        for attempt in range(1, config.MAX_TRIES + 1):
            self._wait_turn()
            self.requests += 1
            resp: httpx.Response | None = None
            try:
                resp = self._client.get(url)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            finally:
                # The delay counts from the end of the previous response.
                self._last_request = time.monotonic()
            if resp is not None:
                if resp.status_code == 200:
                    return resp.text
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code not in _RETRY_STATUS:
                    break
                if resp.status_code == 429:
                    if attempt == config.MAX_TRIES:
                        raise RateLimited(f"{url}: HTTP 429 after {attempt} tries")
                    retry_after = resp.headers.get("Retry-After", "")
                    wait = float(retry_after) if retry_after.isdigit() else config.BACKOFF_BASE_S * 2**attempt
                    wait = min(max(wait, config.BACKOFF_BASE_S * 2**attempt), config.MAX_RETRY_AFTER_S)
                    log.warning("fetch %s rate limited (429), retry %d in %.0fs", url, attempt, wait)
                    time.sleep(wait)
                    continue
            if attempt < config.MAX_TRIES:
                backoff = config.BACKOFF_BASE_S * 2 ** (attempt - 1)
                log.warning("fetch %s failed (%s), retry %d in %.0fs", url, last_error, attempt, backoff)
                time.sleep(backoff)
        raise FetchError(f"{url}: {last_error}")
