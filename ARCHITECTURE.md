# TA-taxonomies — Suites Architecture

> Internals of the taxonomy layer. The cross-repo picture lives in
> `TA-workspace/docs/architecture/SYSTEM.md`; decisions in ADR-0003/0004/0005.
> Grounded in the team's Sprint 1 hands-on findings (one graph per mentee, all
> converging on Neo4j) and their Sprint 2 suite-per-taxonomy design.

## One suite per taxonomy

A suite is the unit of ownership and of deployment: **loader + graph schema +
tools** for one source, behind the shared contract. Suites never import each
other; anything cross-suite lives in `crosswalks/`.

```
src/ta_taxonomies/
├── contract/          # typed tool surface — the ONLY thing TA-agents imports
├── suites/
│   ├── esco/          # loader, schema, tools for ESCO
│   ├── onet/          # … O*NET
│   ├── sfia/          # … SFIA
│   └── bls/           # … BLS/SOC
├── crosswalks/        # explicit cross-taxonomy links only
└── ingestion/         # shared helpers: fetch → normalize → load → validate
```

## The suite contract (`contract/`)

Implemented by every suite; typed I/O with Pydantic v2:

- `search_nodes(text, kind?) → candidates + confidence` — resolution order:
  exact match → alias/label index → vector fallback. The alias/label index is
  first-class: real users type job titles, not identifiers (O*NET publishes
  57k+ lay titles; ESCO altLabels span 28 languages).
- `get_neighbors(node_id, rel_types?) → nodes + edges` — single hop, only over
  relationship types this suite declares **traversable** in its config.
- `enumerate_paths(from_id, to_id, limits) → paths` — multi-hop, depth-capped,
  cycle-free. Pruning (top-K by edge weight) happens **here**, returning counts
  of what was cut — never the cut rows.
- `score_paths(paths, policy) → ranked paths` — scoring under a **named,
  versioned policy**. Where the source has real weights (O*NET importance/level
  with sample sizes and CIs), the policy uses them and says so. Where the
  source is structurally unweighted (**ESCO is binary**: essential/optional,
  no weight field), the policy is *our modeling decision*, declared as such —
  never presented as source data.

## Canonical vocabulary

Suites map their native schema onto the shared vocabulary at load time:

- **Nodes:** `Skill` · `Task` · `Occupation` · `Framework` · `Level` · `Evidence`
- **Edges:** `HAS_SKILL` · `PERFORMS_TASK` · `BROADER_THAN` · `RELATED_TO` ·
  `HAS_LEVEL` · `SUPPORTED_BY` · `MAY_LEAD_TO`

Native richness isn't thrown away: suite-specific properties ride on nodes and
edges (e.g. O*NET importance/level; SFIA responsibility levels as first-class
`Level` nodes, since level-4 ≠ level-7 of the same skill).

### Identity rules

- IDs are **suite-scoped**: `esco:…`, `onet:…`, `sfia:…`, `bls:…`.
- Every node carries **`source` + `source_id`** (codes collide across sources —
  e.g. SFIA's skill code `ISCO` vs. the ISCO occupation classification).
- Preserve source-native identifiers exactly — including string typing for
  codes with leading zeros (ISCO-08 silently corrupts as integers).

## Ingestion pipeline

Every suite loader implements the same stages, runnable end to end:

```
fetch → normalize → load → validate
```

- **fetch** — from the source's native access method (files, API, SPARQL).
  Licensed payloads are fetched at load/run time, **never committed**.
- **normalize** — native schema → canonical vocabulary + suite properties.
- **load** — into Neo4j via batched `MERGE` on suite-scoped IDs. Never MERGE
  satellite nodes by bare value (numeric coincidence merges unrelated
  entities); merge on identity, attach values as properties.
- **validate** — assertions after every load: row counts survive translation,
  no dangling/orphaned edges, no blank nodes, spot-check queries pass.

Reproducibility is a hard requirement: `python -m ta_taxonomies.suites.<x>.load`
rebuilds the suite's graph from source. Tests run against a **small committed
fixture** (license-clean subset), not live sources.

## Crosswalks (`crosswalks/`)

Cross-taxonomy links are **explicit, stored, and cited** — never inferred
silently at query time.

- The occupation hub is **SOC/ISCO**: BLS is SOC-native; O*NET-SOC prefixes to
  SOC; ESCO aligns to ISCO-08 (its SOC bridge is indirect and lossy — say so).
- **SFIA has no occupations** — it bridges **skill-to-skill**, not
  occupation-to-occupation.
- Where no reliable link exists, the answer is **"no link"** — recorded, not
  guessed.

## Licensing (hard rules, from the team's Sprint 1 findings)

- **Pointer, not payload.** This repo is Apache-2.0; most sources do not grant
  us redistribution rights. We commit identifiers, our own schema/metadata,
  and hand-written fixtures — never source prose or dumps.
- Each suite's `README` records its source's terms and what they permit.
- The fifth suite is Sweden JobTech (TA-workspace ADR-0006): live-postings signal, CC0; its claimed ESCO concept mappings must be verified at ingestion, not assumed. Joins after the first four suites are ported.

## Testing

- **Contract tests**: one shared suite-agnostic test battery runs against every
  suite's fixture — all four tools return typed results.
- **Load validation**: the `validate` stage assertions run in CI on fixtures.
- New suite behavior ships with tests; keep CI green (`ruff`, `pytest`).
