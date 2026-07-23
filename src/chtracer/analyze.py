"""Post-traversal analysis.

The ownership graph tells you who controls what on paper. Officer overlap
tells you who is actually operating the structure, which frequently differs
and is often the more useful lead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .client import CompaniesHouseClient
from .models import EntityKind, Trace

_PUNCT = re.compile(r"[^A-Z ]")
_TITLES = {"MR", "MRS", "MISS", "MS", "DR", "SIR", "PROF", "LORD", "LADY", "REV"}
_SUFFIXES = {"JR", "SR", "II", "III", "IV", "OBE", "MBE", "CBE", "QC", "KC"}


def normalise_person_name(raw: str) -> str:
    """Companies House officer names arrive in inconsistent formats:
    'SMITH, John Andrew', 'Mr John Andrew SMITH', 'John SMITH'. Reduce to a
    sorted token set so those collapse together.

    This deliberately does not attempt transliteration handling; see the
    xlit-match project for that problem."""
    s = raw.upper().replace(",", " ")
    s = _PUNCT.sub(" ", s)
    tokens = [t for t in s.split() if t and t not in _TITLES and t not in _SUFFIXES]
    return " ".join(sorted(tokens))


def _dob_key(officer: dict) -> str:
    dob = officer.get("date_of_birth") or {}
    if not dob:
        return ""
    return f"{dob.get('year', '?')}-{dob.get('month', '?')}"


@dataclass
class OfficerLink:
    name: str
    normalised: str
    dob: str
    appointments: list[dict] = field(default_factory=list)

    @property
    def company_count(self) -> int:
        return len({a["company_number"] for a in self.appointments})

    def to_row(self) -> dict:
        return {
            "name": self.name,
            "dob_month_year": self.dob,
            "company_count": self.company_count,
            "companies": "; ".join(
                sorted({f"{a['company_number']} ({a['role']})" for a in self.appointments})
            ),
            "confidence": "high" if self.dob else "low — no DOB to disambiguate",
        }


def cross_reference_officers(
    client: CompaniesHouseClient,
    trace: Trace,
    min_companies: int = 2,
    include_resigned: bool = False,
) -> list[OfficerLink]:
    """Find officers appearing at more than one company in the traced set.

    Matching is on normalised name plus month/year of birth where available.
    Where a DOB is absent (common for corporate officers and older records)
    the match is name-only and should be treated as a lead requiring
    confirmation, not a fact. The confidence column flags these."""
    companies = [
        e.id for e in trace.entities.values()
        if e.kind == EntityKind.COMPANY and not e.id.startswith("foreign:")
    ]

    index: dict[tuple[str, str], OfficerLink] = {}

    for number in companies:
        for officer in client.officers(number):
            if officer.get("resigned_on") and not include_resigned:
                continue
            name = officer.get("name", "")
            if not name:
                continue
            norm = normalise_person_name(name)
            dob = _dob_key(officer)
            key = (norm, dob)
            link = index.setdefault(
                key, OfficerLink(name=name, normalised=norm, dob=dob)
            )
            link.appointments.append(
                {
                    "company_number": number,
                    "role": officer.get("officer_role", ""),
                    "appointed_on": officer.get("appointed_on", ""),
                    "resigned_on": officer.get("resigned_on", ""),
                }
            )

    hits = [l for l in index.values() if l.company_count >= min_companies]
    return sorted(hits, key=lambda l: (-l.company_count, l.normalised))
