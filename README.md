# TA-taxonomies

> **Talent Angels** taxonomy suites — per-taxonomy ingestion pipelines, graph
> schemas, crosswalks, and the suite contract the agents build on.

Part of the [`LFX-Talent-Angels`](https://github.com/LFX-Talent-Angels) org. For
project-wide docs, onboarding, and rules, see
[`TA-workspace`](https://github.com/LFX-Talent-Angels/TA-workspace).

## What a suite is

Each taxonomy is a **suite**: a self-contained package with its own loader,
graph schema, and tools, all behind one shared contract (the team's Sprint 2
design, ratified in ADR-0003/0004):

| Tool | Does |
| ---- | ---- |
| `search_nodes(text, kind?)` | resolution: candidates + confidence |
| `get_neighbors(node_id, rel_types?)` | single-hop traversal |
| `enumerate_paths(from_id, to_id, limits)` | depth-capped, cycle-free routes |
| `score_paths(paths, policy)` | ranking under an explicit, named policy |

The assistant runtime in
[`TA-agents`](https://github.com/LFX-Talent-Angels/TA-agents) consumes this repo
as a **versioned library** and may import only the contract surface.

## Suites

| Suite | Source | Owner model |
| ----- | ------ | ----------- |
| `esco` | ESCO (EU) | one mentee per suite — |
| `onet` | O*NET (US DoL) | the Sprint 1 author of |
| `sfia` | SFIA | that taxonomy's graph |
| `bls`  | BLS / SOC | owns its suite |

The **fifth suite** is **Sweden JobTech** (live-postings signal, CC0) — decided in TA-workspace ADR-0006; it joins after the first four are ported. SFIA is **structure only** (codes, names, levels — never its descriptive text).

## Hard rules (licensing & provenance — see ARCHITECTURE.md)

- **Pointer, not payload**: graphs and this repo store identifiers and our own
  metadata. Licensed source text is fetched at runtime, never committed.
- Every node carries `source` + `source_id`; IDs are suite-scoped
  (`esco:…`, `onet:…`) — no cross-suite identity outside explicit crosswalks.
- Loaders are reproducible (`python -m ta_taxonomies.suites.<suite>.load`) and
  ship with a small committed **fixture** for tests — never full dumps.

## Quick start

```bash
git clone https://github.com/LFX-Talent-Angels/TA-taxonomies.git
cd TA-taxonomies
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # Neo4j credentials etc.
pytest
```

## Layout

```
src/ta_taxonomies/
├── contract/     # the suite contract: typed tool surface (Pydantic v2)
├── suites/       # esco/ onet/ sfia/ bls/ — loader + schema + tools each
├── crosswalks/   # explicit cross-taxonomy links (SOC/ISCO hub; SFIA skill-to-skill)
└── ingestion/    # shared pipeline helpers: fetch → normalize → load → validate
tests/            # contract tests run against each suite's fixture
```

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md). Branch, `git commit -s` (DCO), open a
PR, request a mentor review.

## License

Apache-2.0 — see [`LICENSE`](./LICENSE). This license covers **our code and
schemas only**; taxonomy data remains under its source's terms and is not
redistributed here.
