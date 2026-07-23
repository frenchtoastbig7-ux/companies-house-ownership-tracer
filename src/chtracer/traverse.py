"""Walk the PSC (persons with significant control) chain upwards from a
company to the natural persons behind it -- or to the point where the UK
register stops being able to tell you.

The traversal is breadth-first so that the shallowest path to any entity is
found first, which matters when the same holding company appears at several
depths.
"""

from __future__ import annotations

from collections import deque

from .client import CompaniesHouseClient, NotFound
from .models import (
    Edge,
    Entity,
    EntityKind,
    Termination,
    Trace,
    looks_uk_registered,
    normalise_company_number,
)

# PSC "kind" values returned by the API.
KIND_INDIVIDUAL = "individual-person-with-significant-control"
KIND_CORPORATE = "corporate-entity-person-with-significant-control"
KIND_LEGAL_PERSON = "legal-person-person-with-significant-control"
KIND_SUPER_SECURE = "super-secure-person-with-significant-control"


def _person_id(psc: dict) -> str:
    """People have no stable public identifier. Build one from name plus
    the month/year of birth that Companies House exposes.

    This is a weak key: two directors sharing a name and birth month will
    collide, and the same person filed under 'Jon' and 'Jonathan' will not
    merge. Treat person nodes as candidates for review, not as resolved
    identities."""
    name = (psc.get("name") or "").strip().upper()
    dob = psc.get("date_of_birth") or {}
    stamp = f"{dob.get('year', '?')}-{dob.get('month', '?')}"
    return f"person:{name}|{stamp}"


class OwnershipTracer:
    def __init__(
        self,
        client: CompaniesHouseClient,
        max_depth: int = 5,
        include_ceased: bool = False,
    ):
        self.client = client
        self.max_depth = max_depth
        self.include_ceased = include_ceased

    def trace(self, company_number: str) -> Trace:
        root = normalise_company_number(company_number)
        result = Trace(root=root)

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(root, 0)])

        while queue:
            number, depth = queue.popleft()

            if number in visited:
                # Already expanded. The edge into it is still recorded by the
                # caller, so circular structures show as cycles in the graph
                # rather than hanging the walk.
                node = result.entities.get(number)
                if node and node.termination is None:
                    node.termination = Termination.CYCLE
                continue
            visited.add(number)

            entity = self._fetch_company(number, depth, result)
            if entity.termination is not None:
                continue

            if depth >= self.max_depth:
                entity.termination = Termination.DEPTH_LIMIT
                result.warnings.append(
                    f"{number} ({entity.name}) hit the depth limit of {self.max_depth}; "
                    f"chain above it is unexplored"
                )
                continue

            self._expand(entity, depth, result, queue)

        return result

    # -- internals ---------------------------------------------------------

    def _fetch_company(self, number: str, depth: int, result: Trace) -> Entity:
        source = f"/company/{number}"
        try:
            profile = self.client.company(number)
        except NotFound:
            entity = Entity(
                id=number,
                kind=EntityKind.COMPANY,
                name=f"<unknown {number}>",
                depth=depth,
                termination=Termination.NOT_FOUND,
                source=source,
            )
            result.warnings.append(f"{number} not found on the UK register")
            return result.add_entity(entity)
        except Exception as exc:  # network, auth, malformed response
            entity = Entity(
                id=number,
                kind=EntityKind.COMPANY,
                name=f"<error {number}>",
                depth=depth,
                termination=Termination.ERROR,
                detail={"error": str(exc)},
                source=source,
            )
            result.warnings.append(f"{number} failed to fetch: {exc}")
            return result.add_entity(entity)

        entity = Entity(
            id=number,
            kind=EntityKind.COMPANY,
            name=profile.get("company_name", ""),
            depth=depth,
            detail={
                "company_status": profile.get("company_status", ""),
                "date_of_creation": profile.get("date_of_creation", ""),
                "date_of_cessation": profile.get("date_of_cessation", ""),
                "type": profile.get("type", ""),
                "sic_codes": profile.get("sic_codes", []),
                "jurisdiction": profile.get("jurisdiction", ""),
            },
            source=source,
        )
        if profile.get("company_status") in {"dissolved", "liquidation"}:
            result.warnings.append(
                f"{number} ({entity.name}) is {profile.get('company_status')}; "
                f"its PSC data is a historical snapshot"
            )
        return result.add_entity(entity)

    def _expand(self, entity: Entity, depth: int, result: Trace, queue: deque) -> None:
        number = entity.id
        pscs = self.client.pscs(number)
        source = f"/company/{number}/persons-with-significant-control"

        live = [p for p in pscs if self.include_ceased or not p.get("ceased_on")]

        if not live:
            self._record_no_psc(entity, result)
            return

        for psc in live:
            kind = psc.get("kind", "")

            if kind == KIND_CORPORATE:
                self._handle_corporate(psc, entity, depth, result, queue, source)
            elif kind == KIND_SUPER_SECURE:
                node = result.add_entity(
                    Entity(
                        id=f"suppressed:{number}",
                        kind=EntityKind.SUPER_SECURE,
                        name="<protected PSC — details withheld>",
                        depth=depth + 1,
                        termination=Termination.SUPPRESSED,
                        source=source,
                    )
                )
                result.add_edge(Edge(child=number, parent=node.id, source=source))
                result.warnings.append(
                    f"{number} has a protected PSC; controlling party is not public"
                )
            elif kind == KIND_LEGAL_PERSON:
                pid = f"legal:{(psc.get('name') or '').strip().upper()}"
                node = result.add_entity(
                    Entity(
                        id=pid,
                        kind=EntityKind.LEGAL_PERSON,
                        name=psc.get("name", ""),
                        depth=depth + 1,
                        termination=Termination.RESOLVED,
                        detail=dict(psc.get("identification") or {}),
                        source=source,
                    )
                )
                result.add_edge(self._edge(number, node.id, psc, source))
            else:
                # Default to treating an unrecognised kind as an individual;
                # the raw record is retained in detail for review.
                pid = _person_id(psc)
                node = result.add_entity(
                    Entity(
                        id=pid,
                        kind=EntityKind.INDIVIDUAL,
                        name=psc.get("name", ""),
                        depth=depth + 1,
                        termination=Termination.RESOLVED,
                        detail={
                            "nationality": psc.get("nationality", ""),
                            "country_of_residence": psc.get("country_of_residence", ""),
                            "date_of_birth": psc.get("date_of_birth", {}),
                            "kind": kind,
                        },
                        source=source,
                    )
                )
                result.add_edge(self._edge(number, node.id, psc, source))

    def _handle_corporate(
        self, psc: dict, entity: Entity, depth: int, result: Trace, queue: deque, source: str
    ) -> None:
        ident = psc.get("identification") or {}
        reg = str(ident.get("registration_number") or "").strip()
        name = psc.get("name", "")

        if reg and looks_uk_registered(ident):
            parent_id = normalise_company_number(reg)
            result.add_edge(self._edge(entity.id, parent_id, psc, source))
            queue.append((parent_id, depth + 1))
            return

        # Foreign or unidentifiable corporate parent: the UK register can name
        # it but cannot take you any further. This is where most real chains
        # stop, and it is the finding, not a failure.
        parent_id = f"foreign:{name.upper()}|{reg or 'no-reg'}"
        result.add_entity(
            Entity(
                id=parent_id,
                kind=EntityKind.COMPANY,
                name=name,
                depth=depth + 1,
                termination=Termination.FOREIGN_JURISDICTION,
                detail={
                    "registration_number": reg,
                    "country_registered": ident.get("country_registered", ""),
                    "place_registered": ident.get("place_registered", ""),
                    "legal_form": ident.get("legal_form", ""),
                },
                source=source,
            )
        )
        result.add_edge(self._edge(entity.id, parent_id, psc, source))
        result.warnings.append(
            f"chain leaves the UK register at {name} "
            f"({ident.get('country_registered') or 'jurisdiction not stated'}); "
            f"continuing requires that jurisdiction's registry"
        )

    @staticmethod
    def _edge(child: str, parent: str, psc: dict, source: str) -> Edge:
        return Edge(
            child=child,
            parent=parent,
            nature=list(psc.get("natures_of_control") or []),
            notified_on=psc.get("notified_on", ""),
            ceased_on=psc.get("ceased_on", ""),
            source=source,
        )

    def _record_no_psc(self, entity: Entity, result: Trace) -> None:
        statements = self.client.psc_statements(entity.id)
        current = [s for s in statements if not s.get("ceased_on")]
        entity.termination = Termination.NO_PSC_DECLARED
        if current:
            texts = [s.get("statement", "") for s in current]
            entity.detail["psc_statements"] = texts
            result.warnings.append(
                f"{entity.id} ({entity.name}) declares no PSC: {'; '.join(texts)}"
            )
        else:
            result.warnings.append(
                f"{entity.id} ({entity.name}) has neither PSC entries nor a PSC "
                f"statement — the register may be incomplete or the company exempt"
            )
