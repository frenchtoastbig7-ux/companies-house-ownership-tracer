"""Output formats.

The CSV pair is the important one. Most people who need this output are
analysts working in Excel or i2, not engineers, and a graph they cannot open
is a graph they will not use.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .analyze import OfficerLink
from .models import Trace


def write_json(trace: Trace, path: str | Path) -> None:
    Path(path).write_text(json.dumps(trace.to_dict(), indent=2))


def write_csv(trace: Trace, directory: str | Path, prefix: str = "") -> list[Path]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    written = []

    entities_path = d / f"{prefix}entities.csv"
    rows = [e.to_row() for e in trace.entities.values()]
    rows.sort(key=lambda r: (r["depth"], r["name"]))
    _write_rows(entities_path, rows)
    written.append(entities_path)

    edges_path = d / f"{prefix}relationships.csv"
    edge_rows = [e.to_row() for e in trace.edges]
    # Resolve ids to names so the file is readable on its own.
    names = {e.id: e.name for e in trace.entities.values()}
    for r in edge_rows:
        r["child_name"] = names.get(r["child"], "")
        r["parent_name"] = names.get(r["parent"], "")
    _write_rows(edges_path, edge_rows)
    written.append(edges_path)

    return written


def write_officer_csv(links: list[OfficerLink], path: str | Path) -> Path:
    p = Path(path)
    _write_rows(p, [l.to_row() for l in links])
    return p


def _write_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


_TERMINATION_STYLE = {
    "foreign": ("box", "#c94f4f"),
    "depth-limit": ("box", "#d99b30"),
    "suppressed": ("box", "#8a5fbf"),
    "no-psc-declared": ("box", "#888888"),
    "cycle": ("box", "#3f7fbf"),
    "not-found": ("box", "#888888"),
    "error": ("box", "#888888"),
}


def to_dot(trace: Trace) -> str:
    """Graphviz source. Render with:  dot -Tsvg out.dot -o out.svg"""
    lines = [
        "digraph ownership {",
        '  rankdir=BT;',
        '  node [fontname="Helvetica", fontsize=10, style=filled, fillcolor="#f2f2f2"];',
        '  edge [fontname="Helvetica", fontsize=8, color="#555555"];',
    ]
    for e in trace.entities.values():
        shape = "ellipse" if e.kind.value == "individual" else "box"
        fill = "#f2f2f2"
        if e.termination:
            s = _TERMINATION_STYLE.get(e.termination.value)
            if s:
                shape, fill = s
        label = e.name.replace('"', "'")
        if e.kind.value == "company" and not e.id.startswith("foreign:"):
            label = f"{label}\\n{e.id}"
        if e.termination and e.termination.value not in ("resolved",):
            label = f"{label}\\n[{e.termination.value}]"
        lines.append(
            f'  "{e.id}" [label="{label}", shape={shape}, fillcolor="{fill}"];'
        )
    for edge in trace.edges:
        style = "solid" if edge.is_current else "dashed"
        nature = ", ".join(n.replace("-", " ") for n in edge.nature[:2])
        lines.append(
            f'  "{edge.child}" -> "{edge.parent}" [label="{nature}", style={style}];'
        )
    lines.append("}")
    return "\n".join(lines)


def write_gexf(trace: Trace, path: str | Path) -> None:
    """Gephi format, for anyone who wants to lay the structure out by hand."""
    import networkx as nx

    g = nx.DiGraph()
    for e in trace.entities.values():
        g.add_node(
            e.id,
            label=e.name,
            kind=e.kind.value,
            depth=e.depth,
            termination=e.termination.value if e.termination else "",
        )
    for edge in trace.edges:
        g.add_edge(
            edge.child,
            edge.parent,
            nature="; ".join(edge.nature),
            current=edge.is_current,
        )
    nx.write_gexf(g, str(path))
