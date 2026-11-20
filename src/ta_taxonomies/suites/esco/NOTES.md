# Sprint 4 — ESCO Knowledge Graph

**Author:** Aman Kumar Sarraf (LFX'26 · Talent Angels)  
**Taxonomy:** ESCO (English) · **Graph DB:** Neo4j (local Docker **or** AuraDB)  
**Suite package:** `src/ta_taxonomies/suites/esco/`  
**Branch:** `mentee/AmanSarraf/sprint-04-esco-suite`

---

## Why ESCO (Sprint 4)

Sprint 4 MVP is **ESCO-only**: resolve free text to occupations/skills (Locate),
reveal neighbors (Connect), and eventually skill-gap style Pathfind — with
token/compute cost measured in **TA-agents**.

ESCO is graph-shaped:

- Occupations ↔ skills (essential / optional — **binary**, no native weights)
- Occupations sit under **ISCO-08** groups
- Skills sit under skill / knowledge groups (`broader` hierarchy)

---

## Data source and license

| Item | Detail |
|------|--------|
| **Classification** | ESCO v1.2.1 (English tables) |
| **Portal** | https://esco.ec.europa.eu/ |
| **Download** | https://esco.ec.europa.eu/en/use-esco/download |
| **Package structure** | https://esco.ec.europa.eu/en/structure-esco-downloadable-datasets |
| **License** | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

**Attribution** (when publishing derived materials):

> This work uses the ESCO classification of the European Commission —
> European Skills, Competences, Qualifications and Occupations.
> © European Union. Used under CC BY 4.0.

**Local data (gitignored):** `data/esco/raw/DATABASE/*.xlsx`  
Team pack: `data/esco/ESCO-KNOWLEDGE-GRAPH-20260808.zip` (extract once).

### Files loaded into the graph (English)

| File | Role |
|------|------|
| `occupations_en.xlsx` | Occupation nodes |
| `skills_en.xlsx` | Skill / knowledge nodes |
| `ISCOGroups_en.xlsx` | ISCO-08 group nodes |
| `skillGroups_en.xlsx` | Skill-group nodes |
| `occupationSkillRelations_en.xlsx` | `HAS_SKILL` (essential/optional) |
| `broaderRelationsOccPillar_en.xlsx` | `BROADER_THAN` (occ / ISCO) |
| `broaderRelationsSkillPillar_en.xlsx` | `BROADER_THAN` (skills / groups) |
| `skillSkillRelations_en.xlsx` | `RELATED_TO` (optional) |

**Not loaded (optional collections / metadata):** green/digital/DigComp collections,
`conceptSchemes`, `dictionary`, `greenShareOcc`, etc. Core taxonomy is complete
without them; add later as labels if needed.

**Pointer-not-payload:** full xlsx dumps are **never committed**. Tests use a
small committed fixture under `fixtures/fixture.json`.

---

## Graph model

```text
(:Occupation)-[:HAS_SKILL {relation_type}]->(:Skill)
(:Occupation)-[:CLASSIFIED_UNDER]->(:ISCOGroup)
(:Occupation|:ISCOGroup)-[:BROADER_THAN]->(:Occupation|:ISCOGroup)
(:Skill|:SkillGroup)-[:BROADER_THAN]->(:Skill|:SkillGroup)
(:Skill)-[:RELATED_TO {relation_type}]->(:Skill)
```

| Node | Suite-scoped `id` | Key properties |
|------|-------------------|----------------|
| `:Occupation` | `esco:occupation:<uuid>` | `uri`, `pref_label`, `alt_labels`, `description`, `code`, `isco_group` |
| `:Skill` | `esco:skill:<uuid>` | `skill_type`, `reuse_level`, labels as above |
| `:ISCOGroup` | `esco:isco:<code>` | code **from URI** (string; leading zeros preserved) |
| `:SkillGroup` | `esco:…` | hierarchy groups |

Identity rules: every node has `source = "esco"` and `source_id` = concept URI.

---

## Locate performance: indexes and what they cost

`search_nodes` resolves free text in four tiers (exact preferred label → exact
alias → case-insensitive preferred → substring). All four used to start from
`MATCH (n:EscoNode)` and narrow with `any(l IN labels(n) …)`. From the umbrella
label the planner cannot reach the per-label `pref_label` index, so **every
tier scanned all 21k nodes** — including the exact-match tier, where the index
already existed and sat unused.

```
before:  NodeByLabelScan  rows=21198  dbHits=21199
after:   NodeIndexSeek    rows=1      dbHits=2      RANGE INDEX n:Occupation(pref_label)
```

Indexes created by `apply_schema` (all `IF NOT EXISTS`):

| Index | Serves | Notes |
|-------|--------|-------|
| `esco_<label>_pref` (RANGE) | exact preferred label | needs a **concrete** label in the MATCH |
| `esco_node_text` (FULLTEXT) | alias membership, case-insensitive label, substring | `standard-no-stop-words` over `pref_label` + `alt_labels` |

Why `standard-no-stop-words`: the default `standard` analyzer drops English
stop words, which would make an exact label such as *"one to one
communication"* unfindable through the index while the old scan found it.

Why the full text tiers still re-apply their original predicate in Cypher: the
index is used to **retrieve** candidates, never to score them. Lucene relevance
is not comparable between queries, so it is not mapped onto the confidence
scale in `config.py` — those numbers describe *how* a match was made and stay
exactly as they were. See `tests/suites/esco/test_search_confidence_parity.py`,
which freezes them against the fixture.

Two deliberate opt-outs, both "never slower than before, never different":

- A query term shorter than `MIN_WILDCARD_TERM` (3) makes the infix wildcard
  expand over most of the term dictionary, so those queries take the scan path.
- A graph loaded before this index existed answers from the scan and warns
  `fulltext_index_missing` rather than failing.

`search_nodes` now also reports truncation. A result capped at `SEARCH_LIMIT`
(25) carries `pruning` (`considered` / `returned` / `pruned`), `meta.matches`,
and a `truncated` warning — previously a query with 2,893 matches and one with
25 looked identical to the caller.

### Measuring it

`tests/perf/` builds a deterministic synthetic graph at release scale (21,198
nodes / 161,186 relationships — no ESCO text is committed) and measures against
it. It needs a **throwaway** Neo4j, because it wipes the graph:

```bash
docker run -d --name ta-neo4j-perf -p 7689:7687 \
    -e NEO4J_AUTH=neo4j/perf-dev neo4j:5-community

TA_PERF_NEO4J_URI=bolt://localhost:7689 \
TA_PERF_NEO4J_PASSWORD=perf-dev pytest tests/perf -q

# full before/after table over the frozen 30-query set
PYTHONPATH=src:tests/perf python tests/perf/locate_bench.py \
    --uri bolt://localhost:7689 --password perf-dev
```

The regression test asserts *relatively* (indexed path vs. the scan it
replaced, same process, same graph) and *structurally* (`NodeIndexSeek`, never
`NodeByLabelScan`), because wall-clock thresholds are meaningless on a shared
machine.

Measured on that graph, 30-query set, paired A/B in one process:

| | before | after |
|---|---|---|
| median of per-query medians | 30.3 ms | 4.8 ms (6.3×) |
| mean | 54.6 ms | 9.1 ms (6.0×) |
| slowest query | 249.5 ms | 62.6 ms |
| queries truncated at 25 | 8 | 8 |
| truncation reported | 0 | 8 |
| method + confidence + candidate set | — | identical for all 30 |

The full-text index costs ~0.87 MB on that graph (the range indexes are ~51 MB).

---

## Expected full-load size (English package)

After a successful `--mode full` load you should see approximately:

| Metric | Expected |
|--------|----------|
| Occupations | **~3,039** unique URIs |
| Skills | **~13,939** unique URIs |
| ISCO groups | **~619** |
| Skill groups | **~640** |
| `HAS_SKILL` | **~126,000** |
| `BROADER_THAN` | **~24,000** (occ + skill pillars) |
| `CLASSIFIED_UNDER` | **~3,000** (occ → ISCO where code maps) |
| `RELATED_TO` | **~5,800** (if skill–skill file loaded) |

Exact counts print at the end of the loader. Re-run validation anytime with the
smoke queries in `queries.cypher`.

---

## Reproduce this graph

### Prerequisites

- Python **3.11+**
- [Docker](https://docs.docker.com/get-docker/) **or** a Neo4j Aura instance
- ESCO English tables under `data/esco/raw/DATABASE/` (or set `ESCO_DATA_DIR`)

```bash
cd TA-taxonomies
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip install openpyxl   # required for --mode full (xlsx)
cp .env.example .env   # edit for Aura if needed
```

### Choose a Neo4j backend

| | **Option A — Local Docker** | **Option B — AuraDB Free** |
|---|-----------------------------|----------------------------|
| **Best for** | Offline, full control, wipe/reload | No local DB; shareable demo |
| **URI** | `bolt://localhost:7687` | `neo4j+s://xxxx.databases.neo4j.io` |
| **Browser** | http://localhost:7474 | Aura console → Open |
| **Setup** | `docker compose up -d` | Create free instance at [Aura](https://neo4j.com/cloud/aura-free/) |

Copy `.env.example` → `.env` and set **one** option.

---

### Option A — Local Docker (default for this sprint)

**Credentials (local compose):**

| Field | Value |
|-------|--------|
| Browser URL | http://localhost:7474 |
| Bolt URI | `bolt://localhost:7687` |
| Username | `neo4j` |
| Password | `taxonomies-dev` |
| Database | `neo4j` |

These must match `docker-compose.yml` (`NEO4J_AUTH`) and `.env`.

```bash
# From TA-taxonomies repo root
docker compose up -d
docker compose ps          # wait until healthy

# Full English ESCO KG
export ESCO_DATA_DIR=data/esco/raw/DATABASE
python -m ta_taxonomies.suites.esco.load --mode full
```

**Fixture only** (fast tests / CI slice — **not** the full KG):

```bash
python -m ta_taxonomies.suites.esco.load --mode fixture
```

Stop / wipe:

```bash
docker compose down       # keep volume
docker compose down -v    # wipe graph data volume
```

---

### Option B — Neo4j Aura

1. Create an Aura Free (or higher) instance.
2. Copy the connection URI and password from the Aura console.
3. Put them in `.env`:

```bash
NEO4J_URI=neo4j+s://YOUR_INSTANCE.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=<password-from-aura-console>
NEO4J_DATABASE=neo4j
```

4. **Do not** run `docker compose` for Aura — the same loader talks Bolt to whichever URI is in `.env`.

```bash
export ESCO_DATA_DIR=data/esco/raw/DATABASE
python -m ta_taxonomies.suites.esco.load --mode full
```

**Notes for Aura Free:**

- Full ESCO is larger than the Sprint 1 O\*NET slice; load may take several minutes.
- If the free tier is tight on memory/storage, load `--mode fixture` first to prove connectivity, then full on a larger instance or local Docker.
- Never commit Aura passwords; keep them only in local `.env`.

---

### Verify (same for Docker and Aura)

Open Neo4j Browser and run:

```cypher
// Counts
MATCH (n:Occupation) RETURN count(n) AS occupations;
MATCH (n:Skill) RETURN count(n) AS skills;
MATCH ()-[r:HAS_SKILL]->() RETURN count(r) AS has_skill;

// Spot-check Locate-style lookup
MATCH (o:Occupation)
WHERE toLower(o.pref_label) = 'software developer'
RETURN o.id, o.pref_label, o.uri;

// Skills for an occupation
MATCH (o:Occupation {pref_label: 'software developer'})-[r:HAS_SKILL]->(s:Skill)
RETURN r.relation_type, s.pref_label
ORDER BY r.relation_type, s.pref_label
LIMIT 20;
```

More queries: `queries.cypher` in this folder.

Python smoke:

```bash
python - <<'PY'
from ta_taxonomies.suites.esco.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.esco.tools import EscoSuite

with neo4j_driver() as (driver, database):
    verify_connectivity(driver)
    suite = EscoSuite(driver, database=database)
    r = suite.search_nodes("software developer", kind="occupation")
    print(r.candidates[0].node.id, r.candidates[0].confidence)
PY
```

Contract tests (Neo4j must be up; they reload the **fixture**, wiping full data):

```bash
pytest tests/suites/esco -q
```

Note: `tests/suites/esco/test_load_validate.py` and `test_search_nodes.py` call
`run_load(mode="fixture", wipe=True)`. After tests you must **re-run full load**
if you want the complete KG again.

---

## Loader entrypoint

```text
python -m ta_taxonomies.suites.esco.load --mode fixture|full [--data-dir PATH] [--no-wipe]
```

Pipeline: **fetch (local files) → normalize → MERGE → validate**  
Validation checks: node counts, no blank ids, no dangling `HAS_SKILL`, and
(full mode) every occupation has at least one skill edge.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ServiceUnavailable` / connection refused | `docker compose up -d` or fix Aura URI; check password |
| Auth failed | Align `.env` password with compose `NEO4J_AUTH` or Aura console |
| `openpyxl` missing | `pip install openpyxl` |
| Missing xlsx | Set `ESCO_DATA_DIR` to the folder with `occupations_en.xlsx` |
| Tests wiped my full graph | Expected; re-run `--mode full` |
| ISCO codes look wrong (`1` vs `01`) | Codes must come from URI; loader does this — do not cast codes to int |

---

## Related docs

- `README.md` — short suite overview  
- `data/esco/COMPLETENESS.md` — local dump analysis (gitignored under `/data/`)  
- `SPRINT04-HANDOFF.md` — sprint status  
- `ARCHITECTURE.md` — suite contract and identity rules  
- Sprint 1 O\*NET notes (format inspiration): `TA-lab/mentees/AmanSarraf/sprint-01/NOTES.md`
