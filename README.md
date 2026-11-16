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
| `bls`  | BLS / SOC 2018 + Employment Projections (US, public domain) | owns its suite |
| `jobtech` | JobTech Taxonomy v31 (Sweden, CC0; Swedish labels) | |

SFIA is **structure only** (codes, names, levels — never its descriptive text).
BLS adds wage, outlook and education facts to each occupation; JobTech keeps its
own ESCO matches as URIs (see each suite's `NOTES.md` and TA-workspace ADR-0009).

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
docker compose up -d
pytest
```

All five suites share one local Neo4j (`ta-neo4j`). They are separate logical
graphs (`:EscoNode`, `:OnetNode`, `:SfiaNode`, `:BlsNode`, `:JobtechNode`;
suite-scoped ids). Browser's node total is the **sum**, not a merge.

## Load the full taxonomies

The taxonomy data is **not in git** (`/data/` is gitignored, and licensing
keeps source text out of the repo). Each teammate downloads and loads it once,
into their own Neo4j.

```bash
pip install -e ".[dev,esco-xlsx,bls-xlsx,embed]"   # xlsx readers + the local embedding model
```

| Suite | Where the data comes from | How it gets to `data/` |
| ----- | ------------------------- | ---------------------- |
| ESCO | [ESCO download](https://esco.ec.europa.eu/en/use-esco/download): English, **DATABASE**, xlsx | manual: unzip into `data/esco/raw/DATABASE/` |
| O*NET 31.0 | [O*NET database](https://www.onetcenter.org/database.html) text zip | manual: see `suites/onet/README.md` (pinned SHA-256) |
| SFIA 9 | [SFIA 9 skills](https://sfia-online.org/en/sfia-9/skills/) (structure only) | `sfia.fetch` |
| BLS | bls.gov: [SOC 2018](https://www.bls.gov/soc/2018/) and [Employment Projections](https://www.bls.gov/emp/) xlsx | `bls.fetch` (pinned SHA-256; needs `BLS_USER_AGENT`) |
| JobTech v31 | [JobTech Taxonomy](https://data.jobtechdev.se/taxonomy/version/31/query/) bulk files + GraphQL | `jobtech.fetch` (pinned SHA-256) |

```bash
# ESCO (English DATABASE xlsx under data/esco/raw/DATABASE)
python -m ta_taxonomies.suites.esco.load --mode full

# O*NET 31.0 text dump under data/onet/raw  (see suites/onet/README.md)
python -m ta_taxonomies.suites.onet.load --mode full

# SFIA 9 structure (see suites/sfia/README.md)
python -m ta_taxonomies.suites.sfia.fetch --out data/sfia/sfia9_structure.json
python -m ta_taxonomies.suites.sfia.load --mode full

# BLS: SOC 2018 + Employment Projections (see suites/bls/NOTES.md). bls.gov
# needs a contact in the User-Agent; building reads xlsx (the [bls-xlsx] extra).
BLS_USER_AGENT="TalentAngels-loader/0.1 (you@example.org)" python -m ta_taxonomies.suites.bls.fetch
python -m ta_taxonomies.suites.bls.load --mode full

# JobTech (Sweden), taxonomy version 31 (see suites/jobtech/NOTES.md)
python -m ta_taxonomies.suites.jobtech.fetch
python -m ta_taxonomies.suites.jobtech.load --mode full

# Then embed each suite's labels for meaning-based search (needs the [embed]
# extra). Idempotent; rerun after any bulk re-load.
python -m ta_taxonomies.suites.esco.embed
python -m ta_taxonomies.suites.onet.embed
python -m ta_taxonomies.suites.sfia.embed
python -m ta_taxonomies.suites.bls.embed
python -m ta_taxonomies.suites.jobtech.embed
```

`--mode fixture` is for CI only; it wipes **that suite** and must not be aimed
at a demo database that holds a full graph. A full re-load wipes only its own
suite; `--no-wipe` updates in place and keeps the embeddings.

### Check your load

Run this in Neo4j Browser (http://localhost:7474):

```cypher
MATCH (n) WHERE n.source IN ['esco','onet','sfia','bls','jobtech']
RETURN n.source AS suite, count(n) AS nodes, count(n.label_embedding) AS embedded
ORDER BY suite
```

| Suite | Nodes | Embedded | Notes |
| ----- | ----: | -------: | ----- |
| `bls` | 1,477 | 1,464 | 867 occupations; education and training levels are not embedded |
| `esco` | 18,237 | 18,237 | 3,039 occupations, 13,939 skills |
| `jobtech` | 9,518 | 9,518 | 2,105 occupations, 6,752 skills |
| `onet` | 31,942 | 31,942 | 1,016 occupations, 19,130 tasks |
| `sfia` | 847 | 175 | 147 skills; skill levels are not embedded |

Nodes too low means a fixture or partial load: re-run that suite's `load
--mode full`. Embedded too low: re-run its `embed`.

Cheat-sheet Cypher: `src/ta_taxonomies/suites/esco/queries.cypher` and
`src/ta_taxonomies/suites/onet/queries.cypher`. BLS and JobTech: their
`NOTES.md` lists the schema and the source counts.

## Layout

```
src/ta_taxonomies/
├── contract/     # the suite contract: typed tool surface (Pydantic v2)
├── suites/       # esco/ onet/ sfia/ bls/ jobtech/ — loader + schema + tools each
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
