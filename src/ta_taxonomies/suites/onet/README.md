# O*NET suite

The US Department of Labor's occupational taxonomy: occupations, Content Model
descriptors (skills, knowledge, abilities), and tasks.

## Source & license

- **Source:** O\*NET 30.3 Database, USDOL/ETA — https://www.onetcenter.org/database.html
- **License:** [CC BY 4.0](https://www.onetcenter.org/license_db.html)
- **Download:** `https://www.onetcenter.org/dl_files/database/db_30_3_text.zip`
  — a direct, unauthenticated 13 MB download. No registration, no API key,
  no rate limit; `curl -O` is the whole fetch step.
- **Attribution** (required when publishing derived material):

  > This work includes information from the O\*NET 30.3 Database by the U.S.
  > Department of Labor, Employment and Training Administration (USDOL/ETA).
  > Used under the CC BY 4.0 license. O\*NET® is a trademark of USDOL/ETA.

Full distributions are **not committed** (`data/onet/` is gitignored). Tests use
the small committed fixture under `fixtures/`, which is a slice of real O\*NET
rows — permitted by CC BY 4.0 and reproducible via `fixtures/build_fixture.py`.

## Load

```bash
# A throwaway Neo4j, because a load deletes this suite's nodes.
docker run -d --name ta-neo4j-onet -p 7478:7474 -p 7691:7687 \
    -e NEO4J_AUTH=neo4j/onet-dev neo4j:5-community

export ONET_NEO4J_URI=bolt://localhost:7691
export ONET_NEO4J_PASSWORD=onet-dev

# Fixture (CI / contract tests)
python -m ta_taxonomies.suites.onet.load --mode fixture

# Full distribution (local only)
curl -O https://www.onetcenter.org/dl_files/database/db_30_3_text.zip
unzip db_30_3_text.zip -d data/onet/raw/
export ONET_DATA_DIR=data/onet/raw/db_30_3_text
python -m ta_taxonomies.suites.onet.load --mode full
```

`--mode` has no default: the first thing a load does is delete this suite's
nodes, so the target has to be a choice.

## Graph model

| Labels | Identity |
| --- | --- |
| `OnetNode:OnetOccupation:Occupation` | `onet:occupation:15-1252.00` |
| `OnetNode:OnetElement:Skill` | `onet:element:2.B.3.e` |
| `OnetNode:OnetElementGroup:SkillGroup` | `onet:element:2.B` |
| `OnetNode:OnetTask:Task` | `onet:task:21662` |
| `OnetNode:OnetSocGroup:SOCGroup` | `onet:soc:15-1252` |

Three labels per node, each with a job: `OnetNode` is the umbrella that carries
one unique-`id` constraint for mixed-kind lookups; the `Onet…` label is what
search MATCHes on, so constraints and indexes are this suite's own rather than
shared with ESCO's on the bare canonical label; the canonical label is the
`ARCHITECTURE.md` vocabulary, for anything that wants "all occupations,
whatever the source".

| Rel | Meaning |
| --- | --- |
| `HAS_SKILL` | Occupation → element. Carries `importance`, `level`, sample sizes, standard error, 95% CI bounds, `recommend_suppress`, `not_relevant` |
| `PERFORMS_TASK` | Occupation → Task, with `task_type` |
| `CLASSIFIED_UNDER` | Occupation → SOCGroup |
| `RELATED_TO` | Occupation → Occupation, with `relatedness_tier` |
| `BROADER_THAN` | narrower element → broader element |

## Tools

`OnetSuite` implements the contract: `search_nodes`, `get_neighbors`,
`enumerate_paths`, and — unlike ESCO — a real `score_paths`.

Locate resolves in five tiers: exact source code → exact title → exact lay
title → case-insensitive title → substring. The code tier exists only here,
because an O\*NET-SOC code is an identity a user can actually type. Retrieval
goes through a full-text index from the first load; the index only *retrieves*,
and each tier re-applies its own predicate, so a Lucene relevance score never
becomes a confidence value. Truncation is reported (`pruning` + `truncated`).

`score_paths` ships three named, versioned policies over O\*NET's published
Importance ratings — `onet-importance-bottleneck`, `onet-importance-mean`, and
`onet-importance-lower-ci`, which scores the lower 95% confidence bound so a
rating from a small sample loses to a confident one. An unrecognised policy is
refused rather than approximated.

**Two declared policies that are not source data**, both named in the results
that use them: the Locate confidence scale (`meta["confidence_policy"]`), and
the essential/optional flag on `HAS_SKILL`, which is projected from Importance
so that ESCO-shaped callers keep working (`meta["relation_type_policy"]`).
O\*NET has no notion of an essential skill. See `NOTES.md`.
