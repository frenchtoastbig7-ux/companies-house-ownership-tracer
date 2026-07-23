"""Data model for the ownership graph.

The graph is deliberately simple: nodes are entities (companies, people,
legal persons), edges are control relationships. Everything carries the
provenance of where it came from so an analyst can audit any single claim
back to a Companies House endpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class EntityKind(str, Enum):
    COMPANY = "company"
    INDIVIDUAL = "individual"
    LEGAL_PERSON = "legal-person"
    # A PSC whose details are suppressed by Companies House (protected
    # individuals). We know control exists but not who holds it.
    SUPER_SECURE = "super-secure"


class Termination(str, Enum):
    """Why traversal stopped at a given node. This is the analytically
    interesting field: it tells you *how* a chain ended, which is usually
    more informative than the chain itself."""

    RESOLVED = "resolved"                    # walked to a natural person
    DEPTH_LIMIT = "depth-limit"              # we stopped, not the data
    CYCLE = "cycle"                          # circular ownership, already seen
    FOREIGN_JURISDICTION = "foreign"         # corporate PSC registered outside UK
    NO_PSC_DECLARED = "no-psc-declared"      # company filed a PSC statement instead
    SUPPRESSED = "suppressed"                # protected PSC, details withheld
    NOT_FOUND = "not-found"                  # 404 from the API
    ERROR = "error"                          # request failed


@dataclass
class Entity:
    id: str                 # company number, or a synthetic id for people
    kind: EntityKind
    name: str
    depth: int = 0
    termination: Termination | None = None
    # Free-form detail straight off the API response, kept for audit.
    detail: dict[str, Any] = field(default_factory=dict)
    source: str = ""        # the API path this came from

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "depth": self.depth,
            "termination": self.termination.value if self.termination else "",
            "country": self.detail.get("country_registered", ""),
            "status": self.detail.get("company_status", ""),
            "incorporated": self.detail.get("date_of_creation", ""),
            "source": self.source,
        }


@dataclass
class Edge:
    """child is controlled by parent. Direction runs up the ownership chain."""

    child: str
    parent: str
    nature: list[str] = field(default_factory=list)   # PSC "natures of control"
    notified_on: str = ""
    ceased_on: str = ""
    source: str = ""

    @property
    def is_current(self) -> bool:
        return not self.ceased_on

    def to_row(self) -> dict[str, Any]:
        return {
            "child": self.child,
            "parent": self.parent,
            "nature": "; ".join(self.nature),
            "notified_on": self.notified_on,
            "ceased_on": self.ceased_on,
            "current": self.is_current,
            "source": self.source,
        }


@dataclass
class Trace:
    root: str
    entities: dict[str, Entity] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_entity(self, entity: Entity) -> Entity:
        existing = self.entities.get(entity.id)
        if existing is None:
            self.entities[entity.id] = entity
            return entity
        # Keep the shallowest depth we reached it at.
        if entity.depth < existing.depth:
            existing.depth = entity.depth
        return existing

    def add_edge(self, edge: Edge) -> None:
        for e in self.edges:
            if e.child == edge.child and e.parent == edge.parent:
                return
        self.edges.append(edge)

    def terminations(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.entities.values():
            if e.termination:
                counts[e.termination.value] = counts.get(e.termination.value, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "entities": [asdict(e) for e in self.entities.values()],
            "edges": [asdict(e) for e in self.edges],
            "warnings": self.warnings,
            "summary": {
                "entity_count": len(self.entities),
                "edge_count": len(self.edges),
                "terminations": self.terminations(),
            },
        }


_CH_NUMBER = re.compile(r"^[A-Z0-9]{2}\d{6}$|^\d{8}$")


def normalise_company_number(raw: str) -> str:
    """Companies House numbers are 8 characters. Purely numeric ones are
    commonly written without leading zeros ('1234567'), which the API will
    404 on. Pad them."""
    s = raw.strip().upper().replace(" ", "")
    if s.isdigit():
        s = s.zfill(8)
    return s


def looks_like_company_number(raw: str) -> bool:
    return bool(_CH_NUMBER.match(normalise_company_number(raw)))


# Country strings that appear in PSC identification blocks for UK-registered
# corporate entities. This list is a heuristic, not a specification -- the
# field is free text entered by filers and is inconsistently populated.
# See README "Known limitations".
_UK_HINTS = {
    "ENGLAND", "WALES", "SCOTLAND", "NORTHERN IRELAND",
    "ENGLAND AND WALES", "ENGLAND & WALES", "GREAT BRITAIN",
    "UNITED KINGDOM", "UK", "GB", "ENGLAND AND WALES, UNITED KINGDOM",
}


def looks_uk_registered(identification: dict[str, Any]) -> bool:
    country = str(identification.get("country_registered", "")).strip().upper()
    place = str(identification.get("place_registered", "")).strip().upper()
    if country in _UK_HINTS:
        return True
    if "COMPANIES HOUSE" in place or "UNITED KINGDOM" in place:
        return True
    return False
