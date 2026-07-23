# chtracer

Traces UK beneficial ownership chains through the Companies House API and tells you, explicitly, where and why each chain stops.

Beneficial ownership tracing is a routine task in due diligence and financial-crime work, and it is usually done by hand: look up a company, read the PSC register, look up the corporate PSC, repeat, keep notes in a spreadsheet. That process is slow, hard to audit, and easy to abandon three levels in. This does the walk programmatically and produces an artifact an analyst can hand to someone else.

The design assumption throughout is that **the terminations are the finding**. A chain that resolves cleanly to a named individual is the easy case. What matters in practice is the chain that stops at a Luxembourg SA, or at a protected PSC, or at a company that filed a statement saying it has no PSC at all — because that is where the analytical work starts. Those cases are modelled as first-class outcomes rather than as errors.

## What it does

- Walks the PSC (persons with significant control) register upwards, breadth-first, from any UK company
- Detects circular cross-holdings and terminates instead of hanging
- Distinguishes seven termination reasons: resolved, foreign jurisdiction, depth limit, cycle, no PSC declared, suppressed (protected individual), not found
- Cross-references officers across every company in the traced structure, to surface the people operating the structure as distinct from those owning it on paper
- Outputs CSV (for analysts), JSON (for pipelines), Graphviz DOT and GEXF (for visualisation)
- Caches to disk and self-limits below the API's 600-request/5-minute ceiling

## Install

```bash
git clone https://github.com/<you>/companies-house-ownership-tracer
cd companies-house-ownership-tracer
pip install -e ".[graph]"
```

Register a free REST API key at [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/), then:

```bash
export CH_API_KEY="your-key"
```

## Usage

```bash
# find a company number
chtracer search "Ocado Group"

# trace it
chtracer trace 03977902 --depth 5 --officers --out output/ocado
```

```
tracing 03977902 to depth 5...

12 entities, 14 relationships
  foreign               2
  resolved              7
  no-psc-declared       1

analyst notes:
  - chain leaves the UK register at GAMMA INTERNATIONAL SA (Luxembourg);
    continuing requires that jurisdiction's registry
  - 00000005 (EPSILON DORMANT LIMITED) declares no PSC:
    no-individual-or-entity-with-signficant-control

3 officer(s) appearing at 2+ entities:
  SMITH, John Andrew                       2 companies
  PATEL, Priya                             2 companies

written to output/ocado/  (23 API requests)
render the graph with:  dot -Tsvg output/ocado/graph.dot -o output/ocado/graph.svg
```

Output files:

| file | contents |
|---|---|
| `entities.csv` | one row per entity, with depth, status, jurisdiction, termination reason and the API path it came from |
| `relationships.csv` | control edges with nature of control, notification date, and whether the relationship is current or ceased |
| `officer_overlap.csv` | officers appearing at 2+ entities, with a confidence flag |
| `trace.json` | full structure including raw detail, for downstream processing |
| `graph.dot` | Graphviz source; terminations colour-coded |

Every entity and edge carries a `source` field naming the endpoint it was derived from, so any individual claim in the output can be checked back to the register.

## Known limitations

These are properties of the source data, not bugs, and they matter more than the code does.

**The PSC register is self-declared and unverified.** Companies House does not, in general, check what is filed. A structure designed to obscure control will file something that obscures control. Absence of a PSC entry is not evidence of absence of control.

**Person matching is weak.** People have no public identifier on the register. Person nodes are keyed on normalised name plus month and year of birth, which is all Companies House exposes. Two directors sharing a name and birth month will merge incorrectly; the same person filed as "Jon" and "Jonathan" will not merge at all. The officer cross-reference flags low-confidence matches (`confidence` column) where no date of birth is available. **Treat every person match as a lead requiring confirmation, not as a resolved identity.**

**Jurisdiction detection is heuristic.** Whether a corporate PSC is UK-registered is inferred from the free-text `country_registered` and `place_registered` fields, which filers populate inconsistently. A UK parent recorded unusually will be misclassified as foreign and the chain will terminate early. See `looks_uk_registered()` in `models.py`; the hint list is deliberately conservative, preferring a false termination (visible in the output) over a false traversal (silently wrong).

**Ceased relationships are excluded by default.** Historical structure is often the interesting structure. Use `--include-ceased` when the question is what an arrangement used to look like.

**Dissolved companies return stale PSC data.** Their filings are a snapshot at dissolution. The tool warns on these but does not exclude them.

**Depth limiting is a real constraint, not a formality.** Chains terminated by `--depth` are flagged in the output; a large `depth-limit` count means the trace is incomplete, not that the structure is shallow.

## Design notes

The HTTP layer is injected rather than constructed internally, so the traversal logic is tested against fixtures with no network access and no API key. `tests/fixtures/complex_structure.json` is a synthetic structure containing a UK parent chain, a circular cross-holding, an offshore termination, a ceased PSC and a company declaring no PSC — the cases that break naive implementations. All fixtures are invented; no real individual's data appears in this repository.

38 tests, run on 3.10 and 3.12 in CI:

```bash
pytest -q
```

## Scope and conduct

This uses the official Companies House public API within its terms of use, against a public register that exists precisely so that ownership can be examined. It does not scrape, does not circumvent rate limits, and does not touch any non-public source.

The output concerns identifiable people. Beneficial ownership data is public for a specific purpose, and repurposing it — compiling profiles of individuals, or publishing traces of private persons — is outside what this is for and, depending on jurisdiction, outside what data protection law permits. Use it on entities, in a professional context, for a defensible reason.

## Licence

MIT
