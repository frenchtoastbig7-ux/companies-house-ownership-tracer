from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .analyze import cross_reference_officers
from .client import AuthError, CompaniesHouseClient, RequestsTransport
from .export import to_dot, write_csv, write_json, write_officer_csv
from .models import normalise_company_number
from .traverse import OwnershipTracer


def _client(args) -> CompaniesHouseClient:
    key = args.api_key or os.environ.get("CH_API_KEY", "")
    transport = RequestsTransport(key)
    cache = None if args.no_cache else args.cache_dir
    return CompaniesHouseClient(transport, cache_dir=cache)


def cmd_trace(args) -> int:
    client = _client(args)
    tracer = OwnershipTracer(
        client, max_depth=args.depth, include_ceased=args.include_ceased
    )
    number = normalise_company_number(args.company)
    print(f"tracing {number} to depth {args.depth}...", file=sys.stderr)
    trace = tracer.trace(number)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_json(trace, out / "trace.json")
    write_csv(trace, out)
    (out / "graph.dot").write_text(to_dot(trace))

    if args.officers:
        print("cross-referencing officers...", file=sys.stderr)
        links = cross_reference_officers(client, trace)
        write_officer_csv(links, out / "officer_overlap.csv")
        if links:
            print(f"\n{len(links)} officer(s) appearing at 2+ entities:")
            for link in links[:10]:
                print(f"  {link.name:<40} {link.company_count} companies")

    print(f"\n{len(trace.entities)} entities, {len(trace.edges)} relationships")
    for reason, count in sorted(trace.terminations().items()):
        print(f"  {reason:<20} {count}")
    if trace.warnings:
        print("\nanalyst notes:")
        for w in trace.warnings:
            print(f"  - {w}")
    print(f"\nwritten to {out}/  ({client.request_count} API requests)")
    print("render the graph with:  dot -Tsvg %s/graph.dot -o %s/graph.svg" % (out, out))
    return 0


def cmd_search(args) -> int:
    client = _client(args)
    for item in client.search_companies(args.query, limit=args.limit):
        print(
            f"{item.get('company_number', ''):<10} "
            f"{item.get('company_status', ''):<12} "
            f"{item.get('title', '')}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chtracer",
        description="Trace UK beneficial ownership chains via Companies House.",
    )
    p.add_argument("--api-key", default=None, help="defaults to $CH_API_KEY")
    p.add_argument("--cache-dir", default=".ch-cache")
    p.add_argument("--no-cache", action="store_true")

    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("trace", help="walk the ownership chain")
    t.add_argument("company", help="company number, e.g. 03977902")
    t.add_argument("--depth", type=int, default=5)
    t.add_argument("--out", default="output")
    t.add_argument("--officers", action="store_true", help="cross-reference officers")
    t.add_argument("--include-ceased", action="store_true")
    t.set_defaults(func=cmd_trace)

    s = sub.add_parser("search", help="find a company number by name")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_search)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AuthError as exc:
        print(f"auth error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
