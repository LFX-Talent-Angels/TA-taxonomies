# O*NET Knowledge Graph

**Taxonomy:** O*NET 31.0 (August 2026) · **Graph DB:** Neo4j (local Docker **or** Aura)  
**Suite package:** `src/ta_taxonomies/suites/onet/`

This is the product graph, not a demo slice. `--mode full` loads every
taxonomy-knowledge file from the official **text** dump. `--mode fixture` is
only for tests: a small ICT cut of the **same** schema.

## Source & license

| Item | Detail |
|------|--------|
| Release | O*NET 31.0 |
| Text zip | https://www.onetcenter.org/dl_files/database/db_31_0_text.zip |
| SHA-256 | `6883548adf5fde64cf6f801b35d15519c9225f2732c3cab0e281c652d16b23a9` |
| Dictionary | https://www.onetcenter.org/dictionary/31.0/text/ |
| License | [CC BY 4.0](https://www.onetcenter.org/license_db.html) |

Attribution (verbatim; include the version number):

> This page includes information from the O*NET 31.0 Database by the U.S.
> Department of Labor, Employment and Training Administration (USDOL/ETA).
> Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA.

The website “All Files” button is the **Excel** zip. This loader reads the
**text** zip (tab-delimited `.txt`). Same tables, do not need both.

Local files: `data/onet/raw/` (gitignored). See `README.md` for curl/unzip.

## What is loaded vs skipped

Loaded: occupations, job titles, sample of reported titles, tasks + ratings,
emerging tasks, essential/transferable skills, knowledge, abilities, work
activities/context/styles, software, related occupations, job zones,
education, training, career interests, Content Model + GWA/IWA/DWA,
cross-domain element links, scales and level anchors.

**Not loaded** (survey operations, not taxonomy knowledge):

- `Read Me.txt`
- `Survey Booklet Locations.txt`
- `Occupation Level Metadata.txt`

Measured file list: [`INVENTORY.md`](./INVENTORY.md).

## Graph model

Same Neo4j as ESCO (`ta-neo4j`, `bolt://localhost:7687`). Logical graphs are
separated by `:OnetNode` / `source = "onet"` vs `:EscoNode` / `source = "esco"`.
Community Neo4j has one user database — do not start a second container unless
memory forces it.

```text
(:Occupation)-[:HAS_SKILL {relation_type, importance, level, …}]->(:Skill)
(:Occupation)-[:HAS_KNOWLEDGE|HAS_ABILITY|HAS_WORK_ACTIVITY {importance, level}]->()
(:Occupation)-[:HAS_WORK_CONTEXT|HAS_WORK_STYLE|HAS_INTEREST|HAS_EDUCATION|HAS_TRAINING]->()
(:Occupation)-[:PERFORMS_TASK]->(:Task)
(:Occupation)-[:USES_SOFTWARE {hot_technology, in_demand}]->(:Software)
(:Occupation)-[:RELATED_TO]->(:Occupation)
(:Occupation)-[:HAS_JOB_ZONE]->(:JobZone)
(:IWA)-[:BROADER_THAN]->(:WorkActivity)
(:DWA)-[:BROADER_THAN]->(:IWA)
```

| Node | `id` |
|------|------|
| Occupation | `onet:occupation:15-1252.00` |
| Content-model element | `onet:element:2.B.3.e` (`kind` = Skill, Knowledge, …) |
| Task | `onet:task:<Task ID>` |
| Software | `onet:software:<slug of Workplace Example>` |
| Job zone / scale | `onet:job-zone:4` / `onet:scale:IM` |

Essential vs transferable is `HAS_SKILL.relation_type`, not two id schemes.
Software examples have no O*NET code; slug collision fails the load.

## Load

```bash
docker compose up -d
export ONET_DATA_DIR=data/onet/raw

# Product graph (full 31.0). Wipes only :OnetNode.
python -m ta_taxonomies.suites.onet.load --mode full

# CI / contract tests only — do not run against a demo full graph.
python -m ta_taxonomies.suites.onet.load --mode fixture
```

`--no-wipe` keeps existing O*NET nodes (MERGE updates). Default wipe is
`MATCH (n:OnetNode) WHERE n.source = "onet" DETACH DELETE n` — ESCO stays.

If fixture tests ever replace a full O*NET load, re-run `--mode full`. Same
rule as ESCO: do not point suite pytest at the demo Bolt URL if you care
about the full graph.

Raise Neo4j heap if both full ESCO (~18k nodes) and full O*NET (hundreds of
thousands of rating edges, especially Work Context) sit in one process.

## Expected full-load counts (31.0, measured on this loader)

| | Count |
| --- | ---: |
| Occupations | 1,016 |
| OnetNode (all kinds) | 31,942 |
| Tasks (statements + emerging) | 19,130 |
| Software example nodes | 8,753 |
| HAS_SKILL (essential+transferable, IM+LV paired) | 31,850 |
| USES_SOFTWARE | 31,821 |
| HAS_WORK_CONTEXT | 305,389 |
| HAS_TASK_RATING | 165,780 |

ESCO in the same database is untouched (suite-scoped wipe). Spot-check after load:
`Software Developers` (`onet:occupation:15-1252.00`) should still have job-title aliases such as `Software Engineer`, and `HAS_SKILL` to Programming (`2.B.3.e`) with importance 4.0.

## Locate indexes (schema, before tools)

`apply_schema` creates `onet_node_id` unique, range indexes on O*NET-owned
labels, and full-text `onet_node_text` on `:OnetNode` (`pref_label` +
`alt_labels`, `standard-no-stop-words`). It does **not** recreate ESCO’s
Occupation/Skill unique constraints.
