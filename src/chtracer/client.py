"""Companies House REST client.

Design notes:
  * The HTTP layer is injected (``transport``) so the traversal logic can be
    tested against fixtures with no network. Tests use FixtureTransport.
  * Responses are cached to disk. Ownership structures change on the order of
    months; re-running a trace should not re-hit the API.
  * The public API allows 600 requests per 5-minute window. We self-limit
    below that and honour Retry-After on 429.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

BASE_URL = "https://api.company-information.service.gov.uk"

# Documented limit is 600 requests / 5 minutes. Leave headroom.
RATE_LIMIT_REQUESTS = 550
RATE_LIMIT_WINDOW_SECONDS = 300


class NotFound(Exception):
    """The API returned 404 for this resource."""


class AuthError(Exception):
    """The API rejected our credentials."""


class Transport(Protocol):
    def get(self, path: str, params: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, str]]:
        """Return (status_code, parsed_json_body, headers)."""
        ...


class RequestsTransport:
    """Real HTTP transport. Auth is HTTP Basic with the API key as the
    username and an empty password."""

    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: float = 30.0):
        import requests  # imported lazily so tests need no dependency

        if not api_key:
            raise AuthError(
                "No API key. Set CH_API_KEY, or pass --api-key. "
                "Register a REST key at https://developer.company-information.service.gov.uk/"
            )
        self._session = requests.Session()
        self._session.auth = (api_key, "")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def get(self, path: str, params: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, str]]:
        resp = self._session.get(
            f"{self._base_url}{path}", params=params, timeout=self._timeout
        )
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {}
        return resp.status_code, body, dict(resp.headers)


class RateLimiter:
    """Sliding-window limiter. Blocks rather than raising -- a trace that
    takes an extra minute is fine, a trace that dies at depth 4 is not."""

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_REQUESTS,
        window: float = RATE_LIMIT_WINDOW_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max = max_requests
        self._window = window
        self._sleep = sleep
        self._clock = clock
        self._hits: deque[float] = deque()

    def acquire(self) -> None:
        now = self._clock()
        while self._hits and now - self._hits[0] > self._window:
            self._hits.popleft()
        if len(self._hits) >= self._max:
            wait = self._window - (now - self._hits[0]) + 0.1
            if wait > 0:
                self._sleep(wait)
            return self.acquire()
        self._hits.append(now)


class Cache:
    def __init__(self, directory: str | os.PathLike[str] | None):
        self.dir = Path(directory) if directory else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, path: str, params: dict[str, Any]) -> str:
        blob = json.dumps([path, sorted(params.items())], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if not self.dir:
            return None
        f = self.dir / f"{self._key(path, params)}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            return None

    def put(self, path: str, params: dict[str, Any], body: dict[str, Any]) -> None:
        if not self.dir:
            return
        f = self.dir / f"{self._key(path, params)}.json"
        try:
            f.write_text(json.dumps(body))
        except OSError:
            pass


class CompaniesHouseClient:
    def __init__(
        self,
        transport: Transport,
        cache_dir: str | os.PathLike[str] | None = ".ch-cache",
        rate_limiter: RateLimiter | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._t = transport
        self._cache = Cache(cache_dir)
        self._limiter = rate_limiter or RateLimiter(sleep=sleep)
        self._max_retries = max_retries
        self._sleep = sleep
        self.request_count = 0

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        cached = self._cache.get(path, params)
        if cached is not None:
            return cached

        for attempt in range(self._max_retries):
            self._limiter.acquire()
            self.request_count += 1
            status, body, headers = self._t.get(path, params)

            if status == 200:
                self._cache.put(path, params, body)
                return body
            if status == 404:
                raise NotFound(path)
            if status in (401, 403):
                raise AuthError(f"{status} for {path} -- check your API key")
            if status == 429:
                retry_after = float(headers.get("Retry-After", 5))
                self._sleep(retry_after)
                continue
            if 500 <= status < 600:
                self._sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Unexpected status {status} for {path}")

        raise RuntimeError(f"Giving up on {path} after {self._max_retries} attempts")

    def _paginate(self, path: str, per_page: int = 100) -> Iterator[dict[str, Any]]:
        """Walk a paginated list endpoint. Guards against a malformed
        total_results causing an infinite loop."""
        start = 0
        seen = 0
        while True:
            body = self._get(path, items_per_page=per_page, start_index=start)
            items = body.get("items") or []
            for item in items:
                yield item
            seen += len(items)
            total = body.get("total_results", seen)
            if not items or seen >= total:
                return
            start += len(items)

    # -- endpoints ---------------------------------------------------------

    def company(self, number: str) -> dict[str, Any]:
        return self._get(f"/company/{number}")

    def pscs(self, number: str) -> list[dict[str, Any]]:
        try:
            return list(self._paginate(f"/company/{number}/persons-with-significant-control"))
        except NotFound:
            # Companies with no PSC register return 404 here rather than an
            # empty list. Not an error condition.
            return []

    def psc_statements(self, number: str) -> list[dict[str, Any]]:
        try:
            return list(
                self._paginate(f"/company/{number}/persons-with-significant-control-statements")
            )
        except NotFound:
            return []

    def officers(self, number: str) -> list[dict[str, Any]]:
        try:
            return list(self._paginate(f"/company/{number}/officers"))
        except NotFound:
            return []

    def search_companies(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        body = self._get("/search/companies", q=query, items_per_page=limit)
        return body.get("items") or []
