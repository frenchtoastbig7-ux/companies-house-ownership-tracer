"""Test doubles. No network, no API key, no fixtures recorded from real
individuals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureTransport:
    """Serves canned responses keyed by API path. Records every call so tests
    can assert on request counts (cache behaviour, cycle avoidance)."""

    def __init__(self, fixture: str | dict[str, Any]):
        if isinstance(fixture, str):
            data = json.loads((FIXTURES / fixture).read_text())
        else:
            data = fixture
        self.data = {k: v for k, v in data.items() if not k.startswith("_")}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, params: dict[str, Any]):
        self.calls.append((path, dict(params)))
        if path in self.data:
            return 200, self.data[path], {}
        return 404, {"errors": [{"error": "not-found"}]}, {}

    def paths_called(self) -> list[str]:
        return [c[0] for c in self.calls]


class PagedTransport:
    """Returns `total` synthetic items across pages, to exercise _paginate."""

    def __init__(self, total: int, page_size: int = 100):
        self.total = total
        self.page_size = page_size
        self.calls = 0

    def get(self, path: str, params: dict[str, Any]):
        self.calls += 1
        start = int(params.get("start_index", 0))
        size = int(params.get("items_per_page", self.page_size))
        items = [
            {"name": f"ITEM {i}", "kind": "individual-person-with-significant-control"}
            for i in range(start, min(start + size, self.total))
        ]
        return 200, {"items": items, "total_results": self.total}, {}


class ScriptedTransport:
    """Returns a fixed sequence of (status, body, headers) responses."""

    def __init__(self, responses: list[tuple[int, dict, dict]]):
        self.responses = list(responses)
        self.calls = 0

    def get(self, path: str, params: dict[str, Any]):
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return 200, {}, {}
