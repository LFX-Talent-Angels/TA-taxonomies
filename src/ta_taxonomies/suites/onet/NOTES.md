# O*NET suite — build notes

**Taxonomy:** O\*NET 30.3 (2019 O\*NET-SOC, built on 2018 SOC) · **Graph DB:** Neo4j 5
**Suite package:** `src/ta_taxonomies/suites/onet/`

The ESCO suite is the template this follows. What is written down here is
mostly where O\*NET **differs** from it, because that is what a reader coming
from `suites/esco/` will get wrong.

---

## Why this suite, and what it unlocks

ADR-0006 fixes the source set; O\*NET is CC BY 4.0, costs nothing, and downloads
without registration. The reason it came second rather than fifth is narrower
than "it is good data":

**It is the only adopted source with weights on its edges.** Every
occupation-descriptor pair carries Importance *and* Level, each with a sample
size, a standard error and 95% confidence bounds. That makes `score_paths` —
a deliberate stub in ESCO, because ESCO has no field to hold a number —
implementable from source data for the first time. Everything else in this file
is downstream of that one fact.

---

## Data source and license

| Item | Detail |
|------|--------|
| **Release** | O\*NET 30.3 |
| **Download** | `https://www.onetcenter.org/dl_files/database/db_30_3_text.zip` (13 MB) |
| **Database page** | https://www.onetcenter.org/database.html |
| **License** | [CC BY 4.0](https://www.onetcenter.org/license_db.html) |

**The download needs no registration.** Verified 2026-08-24: a plain
unauthenticated `GET` returns the zip (HTTP 200, 13,222,549 bytes); a range
request returns 206. No account, no API key, no rate limit, no click-through.
The whole fetch step is `curl -O`. (This is not true of every O\*NET service —
the *Web Services API* does require a registered key — but the bulk database
distribution, which is what this loader reads, does not.)

Local data lives under `data/onet/` and is gitignored. The committed fixture is
a small slice of real rows, which CC BY 4.0 permits with attribution.

### Files read (10 of the 45 shipped)

| File | Role |
|------|------|
| `Occupation Data.txt` | Occupation nodes |
| `Abilities.txt` | `HAS_SKILL` (1.A branch) |
| `Essential Skills.txt` | `HAS_SKILL` (2.A basic skills) |
| `Transferable Skills.txt` | `HAS_SKILL` (2.B cross-functional skills) |
| `Knowledge.txt` | `HAS_SKILL` (2.C knowledge) |
| `Content Model Reference.txt` | element names, descriptions, hierarchy |
| `Task Statements.txt` | Task nodes + `PERFORMS_TASK` |
| `Job Titles.txt` | lay titles → `alt_labels` |
| `Sample of Reported Titles.txt` | reported titles → `alt_labels` |
| `Related Occupations.txt` | `RELATED_TO` |

**Deliberately not read.** Work Activities (4.\*) and Work Context (5.\*) share
the same rated-file schema and would drop straight in, but they describe *the
job*, not what a person brings to it, and `HAS_SKILL` means the latter. Loading
them under the same edge type would quietly change what a skills question
answers. Interests, Work Styles, Job Zones, Education, and the cross-domain
join tables are deferred for the same reason: they need their own edge types
first. `Technology Skills` / software tools are a separate node kind (the
Sprint 1 prototype modelled them as `:Software`) and are worth adding, but they
are tools rather than descriptors and do not belong on `HAS_SKILL` either.

---

## Graph model

```text
(:OnetOccupation)-[:HAS_SKILL {importance, level, …}]->(:OnetElement)
(:OnetOccupation)-[:PERFORMS_TASK {task_type}]->(:OnetTask)
(:OnetOccupation)-[:CLASSIFIED_UNDER]->(:OnetSocGroup)
(:OnetOccupation)-[:RELATED_TO {relatedness_tier}]->(:OnetOccupation)
(:OnetElement|:OnetElementGroup)-[:BROADER_THAN]->(:OnetElementGroup)
```

Every node carries three labels — `:OnetNode`, a suite-scoped concrete label,
and the canonical `ARCHITECTURE.md` one. See **Labels** below for why.

### Identity: codes, not URIs

ESCO's identity arrives as a URI that names its own type
(`…/esco/occupation/<uuid>`). O\*NET's arrives as a bare code whose type is
implied by the file it came from and whose *structure* carries meaning. Both
facts are used:

| | |
|---|---|
| `15-1252.00` | O\*NET-SOC code → `onet:occupation:15-1252.00` |
| `15-1252` | the 2018 SOC code it extends → `onet:soc:15-1252` |
| `2.B.3.e` | Content Model Element ID → `onet:element:2.B.3.e`; the dots *are* the hierarchy, so `BROADER_THAN` needs no join table |

Codes stay strings. `15-1252.00` read as a float is `15 - 1252.0`; read as "a
number with a decimal point" it loses the trailing zero and stops matching the
source. Neither is recoverable afterwards.

**The `.00` suffix is not decoration.** `.00` means the occupation is
coextensive with its SOC code; `.01` and up are detailed O\*NET occupations that
subdivide one. In release 30.3: 867 SOC codes, 1,016 occupations, 76 SOC codes
split into 149 detailed occupations. A crosswalk landing on `onet:soc:15-1252`
therefore resolves to *N* occupations, not one.

**The SOC node is O\*NET's view of SOC, not SOC itself.** It is derived from the
published code prefix — a documented decomposition, not an inference — and it
lives in this suite because the derivation is O\*NET's own. The authoritative
SOC nodes belong to the BLS suite when it exists, and linking `onet:soc:X` to
`bls:soc:X` is a crosswalk's job.

---

## Where the shared contract met ESCO's assumptions

The contract's *shapes* are source-agnostic. Its *semantics*, and the code
calling it, are not. Four places, in descending order of how much they hurt.

### 1. `relation_type` is an ESCO concept, and its absence fails silently

`TA-agents/src/talent_angels/skills/connect/reveal.py:94` filters neighbours
with `edge.properties.get("relation_type") == request.relation_kind`, and the
phrase router defaults that kind to `"essential"` for the commonest question
there is — `connect_request.py:116`, `:132`, `:138`, and
`agent_loop.py:369-370`. O\*NET has no notion of an essential skill, so against
an unmodified O\*NET graph that filter matches nothing and the agent reports
*no skills at all*. Not an error — an empty list is a valid answer, so nothing
anywhere would flag it.

**What this suite does:** the loader writes a `relation_type` of
essential/optional projected from Importance (`>= 3.0`, the midpoint of the
published 1–5 scale), and stamps `relation_type_policy` on the same edge;
`get_neighbors` repeats the policy in `meta`. Projecting a published number onto
a coarser binary is lossy but honest, and it is the opposite of what ADR-0006
forbids — which is inventing a *number* from ESCO's binary.

**What it does not do:** touch TA-agents. The threshold is one constant
(`ESSENTIAL_IMPORTANCE_MIN`), and a caller that wants the real distribution
should read `importance` and ignore the flag. The proper fix is for
`relation_kind` to be a suite-interpreted concept rather than a literal
property comparison, and that is a change to another repo.

### 2. Confidence is a bare float on an undeclared scale

`Candidate.confidence` (`contract/models.py:143`) is a `float` in `[0, 1]` with
no room to say what it measures. ESCO's numbers
(`suites/esco/config.py:56-60`) and this suite's are **not comparable**, for two
structural reasons rather than tuning:

- **Granularity.** O\*NET describes the whole US labour market in 1,016
  occupations; ESCO uses 3,039. The same "0.95" resolves to a coarser thing
  here.
- **What an alias is.** ESCO's `altLabels` are curated synonyms. O\*NET's alias
  pool is 57,543 *lay* job titles plus 7,953 reported titles — deliberately
  including employer-specific and idiosyncratic ones. An exact alias hit is
  weaker evidence here, so `CONF_EXACT_ALT` is 0.82 against ESCO's 0.90 *for a
  different reason than being tuned lower*.

**What this suite does:** declares `CONFIDENCE_POLICY` (name + version) and
returns it in `meta["confidence_policy"]` on every search result. That does not
make the scales comparable; it makes the incomparability *visible*, which is
the part a fusion layer can act on. Today ESCO returns no such marker, so two
suites' candidate lists are not merely on different scales but
indistinguishable — sorting a combined list by `confidence` is currently a bug
waiting to be written. Fixing it properly means a calibration that maps both
onto one scale, and that is a decision to be taken, not slipped in here.

### 3. `Edge.type` is a free string, and `get_neighbors` rejects the unknown

`Edge.type` is `str` (`contract/models.py:56`), so nothing type-checks a
relationship name — but `get_neighbors` refuses types the suite does not
declare traversable. The Sprint 1 O\*NET prototype called the descriptor edge
`REQUIRES_SKILL`; every caller in TA-agents passes `HAS_SKILL`. A suite that
kept the prototype's name would answer `unknown_rel_types` to the only question
anyone asks. This suite therefore uses the `ARCHITECTURE.md` canonical
vocabulary, as the architecture requires ("suites map their native schema onto
the shared vocabulary at load time").

The remaining mismatch is the other way round: `PERFORMS_TASK` and `RELATED_TO`
are real O\*NET edges that no TA-agents call site ever asks for, so those parts
of the graph are unreachable through the agent today. That is a caller
limitation, not a defect here.

### 4. `SuiteName` is a closed Literal — and it was already right

`contract/models.py:17` pins `SuiteName` to five values. `"onet"` is one of
them, so nothing needed changing. Worth recording as a non-problem, because it
is the sort of thing a third suite might have had to file a contract PR for.

---

## Real counts (release 30.3, full load)

Loaded 2026-08-24 into a throwaway Neo4j 5, `--mode full`.

| Nodes | | Relationships | |
|---|---|---|---|
| Occupations | **1,016** | `HAS_SKILL` | **107,280** |
| SOC groups | **867** | `PERFORMS_TASK` | **18,796** |
| Elements | **120** | `RELATED_TO` | **18,460** |
| Element groups | **40** | `CLASSIFIED_UNDER` | **1,016** |
| Tasks | **18,796** | `BROADER_THAN` | **158** |
| **Total** | **20,839** | **Total** | **145,710** |

Occupation `alt_labels` hold **57,540** lay titles.

Content Model elements by branch:

| Branch | Elements | Rated pairs |
|---|---|---|
| 1.A Abilities | 52 | 46,488 |
| 2.A Basic skills | 10 | 8,940 |
| 2.B Cross-functional skills | 25 | 22,350 |
| 2.C Knowledge | 33 | 29,502 |
| **Total** | **120** | **107,280** |

**A number in ADR-0006 to re-read.** The ADR describes "62,580 skill-occupation
edges rated on Importance *and* Level". 62,580 is exactly
`17,880 + 44,700` — the **row** counts of the two skills files. Since O\*NET
publishes Importance and Level as separate rows for the same pair, that is
31,290 *edges*, not 62,580. The ADR's conclusion is unaffected (O\*NET is the
weighted backbone) but the figure is rows, and with knowledge and abilities
included the real edge count is 107,280.

### Facts that break ESCO-shaped assumptions

- **122 of the 1,016 occupations carry no descriptor ratings at all** (894 are
  rated). They are mostly "All Other" residual categories. ESCO's loader
  asserts that every occupation has at least one skill edge; the same assertion
  here would refuse to load correct data, so `validate_load` does not make it.
- **5,076 `HAS_SKILL` edges are flagged `Recommend Suppress`** — O\*NET's own
  marker for a rating too unreliable to display. O\*NET OnLine hides them.
- **16,830 are flagged `Not Relevant`** for that occupation.
- **7,808 knowledge edges have no confidence bounds**, so the lower-CI scoring
  policy has nothing to read on 7.3% of edges and falls back to the declared
  neutral value.
- **Every element is a hub.** 120 elements carry 107,280 edges: "Reading
  Comprehension" links to 894 occupations. This is the single biggest
  structural difference from ESCO's 13,939 skills, and it is what forced the
  traversal changes below.

---

## Locate: indexed from the first load, not after a regression

ESCO's `search_nodes` matched the umbrella label `:EscoNode` and narrowed with
`any(l IN labels(n) …)`. From the umbrella the planner cannot reach a per-label
index, so every tier scanned the whole graph — including the exact-match tier,
where the index already existed and sat unused (PR #9, ~6× on a release-scale
graph).

This suite starts from the fixed shape: **exact tiers seek concrete labels**
(one arm per label, unioned), and the alias / case-insensitive / substring tiers
retrieve through a full-text index (`onet_node_text`,
`standard-no-stop-words` over `pref_label` and `alt_labels`). O\*NET makes the
scan path worse than it ever was for ESCO — 57k lay titles over 1,016
occupations — so shipping the index with the first load is not premature.

The index only **retrieves**. Each tier re-applies its original predicate in
Cypher, so a Lucene relevance score never becomes a confidence value: relevance
is not comparable across queries, and the confidence scale describes *how* a
match was made. Two opt-outs, both "never slower, never different": a query term
shorter than `MIN_WILDCARD_TERM` takes the scan path, and a graph without the
index answers from the scan with a `fulltext_index_missing` warning.

**One tier ESCO does not have:** an exact match on the source code, tried first.
`15-1252.00` is an identity a user can actually type, and without this tier it
would fall through to a substring search that also matches whatever else
contains those digits.

Truncation is reported: a capped result carries `pruning`, `meta["matches"]`,
and a `truncated` warning rather than silently returning the first 25 of an
unknown many.

---

## Traversal: two changes O*NET's shape forced

Both were found by running the tool against the real graph, not by reading it.

**1. The branching cap is applied per relationship type.** Expansion is ordered
by descending Importance so that when the cap bites it keeps the strongest
edges — possible here and not in ESCO, because the ordering is a published
number rather than a tie-break invented to make the cut deterministic. But only
`HAS_SKILL` has an Importance. Under one global cap every unrated edge sorted
last and was cut first, so an occupation with 120 rated descriptors never
expanded a single `RELATED_TO` edge and occupation-to-occupation routes were
unreachable. Per type, each kind of hop keeps its own strongest 15.

**2. A hop that lands on the target is exempt from the cap.** With 120 elements
carrying 107k edges, an element's fifteen strongest neighbours are the fifteen
occupations that rate it highest — almost never the one being asked about.
Before this, `enumerate_paths` between two software occupations returned **zero
paths** while reporting a large, healthy-looking `pruned` count: the cap was
hiding the answer rather than bounding the search. The cap exists to limit how
many *unknown* branches to follow; an edge that arrives at the target is not
speculative.

---

## `score_paths`: implemented, and what that actually buys

Three named, versioned policies, all normalising Importance to `[0,1]` over the
published 1–5 scale:

| Policy | Rule |
|---|---|
| `onet-importance-bottleneck/1` | minimum over the path — a route is as strong as its weakest hop |
| `onet-importance-mean/1` | arithmetic mean — rewards paths strong on average |
| `onet-importance-lower-ci/1` | minimum over the **lower 95% confidence bound** |

The third is the one that could not exist without this source. A rating from
eight respondents with a wide interval loses to one from thirty with a narrow
one, *without anyone hand-weighting sample size* — the penalty is already in
the published statistics. Measured on the full graph, ranking paths between
`Programming` and `Computers and Electronics`, the point-estimate policies rank
Computer Programmers first while the lower-CI policy demotes routes whose
evidence is thin. The three policies genuinely disagree; that is the point of
naming them.

Declared, and not source data:

- An unrated hop (`PERFORMS_TASK`, `RELATED_TO`, `CLASSIFIED_UNDER`,
  `BROADER_THAN`) contributes `UNWEIGHTED_EDGE_SCORE = 0.5`. The neutral
  midpoint neither rewards nor punishes structural routing; any other value
  would be a claim O\*NET does not make.
- A `Recommend Suppress` edge is **neutralised to that same 0.5, not dropped**.
  Dropping it would shorten the path's evidence and, under a minimum, *raise*
  its score — rewarding a route precisely because its data was untrustworthy.
  The count is reported in the result's warnings.
- An unknown policy name, or a known name at an unknown version, is **refused**.
  Returning a number under a name that never defined one is the failure
  `PolicyRef` exists to prevent.

---

## Labels: why three, and the constraint question

Nodes carry `:OnetNode` (umbrella, one unique-`id` constraint for mixed-kind
lookups), a suite-scoped concrete label (`:OnetOccupation` — what search matches
on), and the canonical `ARCHITECTURE.md` label (`:Occupation` — for cross-suite
reads).

The suite prefix on the concrete label was adopted on the assumption that
Neo4j 5 would *reject* a second uniqueness constraint on `:Occupation(id)`,
since ESCO already declares one as `esco_occupation_id`. **Measured, that is
wrong, and what actually happens is worse.** Against Neo4j 5 community:

```
CREATE CONSTRAINT esco_occupation_id IF NOT EXISTS FOR (n:Occupation) REQUIRE n.id IS UNIQUE
CREATE CONSTRAINT onet_occupation_id IF NOT EXISTS FOR (n:Occupation) REQUIRE n.id IS UNIQUE
SHOW CONSTRAINTS … → ['esco_occupation_id']          # the second is a silent no-op
```

Without `IF NOT EXISTS` it is a hard `Neo.ClientError.Schema.ConstraintAlreadyExists`;
with it — which is what every `schema.py` in this repo uses — it succeeds and
creates nothing. Indexes behave identically. So a suite indexing the canonical
label does not fail; it silently inherits another suite's schema objects, and
`DROP CONSTRAINT esco_occupation_id` would remove O\*NET's uniqueness guarantee
with no message anywhere. The prefixed label is still the right answer — for
this reason rather than the one first assumed.

---

## MERGE identity: a duplicate that validated clean

`_merge_nodes` originally merged on the *kind* label —
`MERGE (n:OnetOccupation {id})` — then applied the umbrella and canonical
labels. MERGE matches on the whole pattern, **labels included**, so a node
already holding that id under a different label set is invisible to it and gets
duplicated rather than matched. Measured against a graph seeded with a single
`(:Occupation {id: 'onet:occupation:15-1252.00'})` placeholder:

```
nodes carrying onet:occupation:15-1252.00 -> 2
    ['Occupation']                                 crosswalk-placeholder
    ['Occupation', 'OnetNode', 'OnetOccupation']   onet
```

The load **succeeded and validated clean**. The uniqueness constraint could not
see it, because constraints are per label and the stale node carried neither
`:OnetNode` nor `:OnetOccupation`; every count still added up, because the count
queries are label-scoped too. Two nodes shared one id and nothing said so.

This is not hypothetical. A crosswalk that materialises an endpoint before its
suite is loaded leaves exactly such a node, and the wipe does not remove it —
deleting another package's node is not this loader's call. (The same bug, in a
worse form, was hit independently in `crosswalks/`, where the duplicate
multiplied relationship matches: 8 rows matched 32.)

Two changes, because neither alone is enough:

* **MERGE on `:OnetNode`**, the one label every node of this suite carries and
  the one the uniqueness constraint is on. That makes the identity the umbrella
  rather than the kind, and keeps the lookup constraint-backed and indexed.
* **`validate_load` fails on any node whose id starts with `onet:` and which
  lacks `:OnetNode`.** No indexed MERGE can be immune to a node that lacks the
  label it merges on, so the remaining case is detected and named rather than
  merged around. It names the count and says to label or remove them; it does
  not delete them.

The general rule is ARCHITECTURE.md's "MERGE on identity, never on a composite
that includes incidental structure" — with the reminder that in Cypher, **a
label is part of that composite**.

### The same class, elsewhere: last-wins keys

The defect above is one instance of a wider one: *a key collision that resolves
silently is indistinguishable from the data never having existed.* Every
dictionary in `normalize_document` was audited against release 30.3 for it:

| Key | Duplicates in 30.3 | Guarded |
|---|---|---|
| (occupation, element, scale) → rating | 0 of 214,560 rows | conflicting values now raise |
| Task ID → statement | 0 of 18,796 | two statements for one id now raise |
| Element ID → Content Model row | 0 of 3,006 | bijective with the node id |
| (from, to) → edge | 0 | collapse count now printed |
| O\*NET-SOC code → occupation | 0 of 1,016 | bijective with the node id |

Nothing collides today, which is exactly why the guards are worth having: they
exist for the release that changes shape, not for this one. An identical repeat
stays harmless; only a *conflicting* one stops the load.

Codes need no separate uniqueness check here because every id is derived from
its code (`onet:occupation:<code>`), so a duplicate code is a duplicate id and
the constraint already covers it. That is not true of a package whose join key
is a property rather than the id — `crosswalks/` joins on the ESCO code, which
no constraint declares unique, and had to add its own detection.

### Where these bugs were found

Three real defects came out of this work — the constraint no-op, the
label-set MERGE, and `crosswalks/`' ambiguous join key. **None was found by
reasoning and none by a passing test suite; all three were found by running
against a graph that already had another suite's nodes in it.** All three
loaded, validated clean, and reported counts that added up.

Post-load validation that only ever runs against an empty graph cannot catch
this class at all, because every one of these failures needs a pre-existing
node to express itself. Worth treating as a repo-wide testing rule rather than
a note in one suite's file.

---

## Reproduce

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# A throwaway Neo4j. A load DELETES this suite's nodes, so never point it at
# a graph you care about.
docker run -d --name ta-neo4j-onet -p 7478:7474 -p 7691:7687 \
    -e NEO4J_AUTH=neo4j/onet-dev neo4j:5-community

export ONET_NEO4J_URI=bolt://localhost:7691
export ONET_NEO4J_PASSWORD=onet-dev

curl -O https://www.onetcenter.org/dl_files/database/db_30_3_text.zip
unzip db_30_3_text.zip -d data/onet/raw/

python -m ta_taxonomies.suites.onet.load --mode fixture
python -m ta_taxonomies.suites.onet.load --mode full \
    --data-dir data/onet/raw/db_30_3_text

pytest tests/suites/onet -q
```

`ONET_NEO4J_*` overrides `NEO4J_*` for exactly this reason: every suite reads
the same `NEO4J_URI`, and one stale export is enough to point a wiping loader at
the graph someone else was using. The resolved URI is printed *before* anything
is deleted.

Tests that need Neo4j carry `pytest.mark.neo4j` and skip when it is absent;
they reload the fixture with `wipe=True`, so re-run `--mode full` afterwards if
you wanted the complete graph.

---

## Not done

- **Work Activities, Work Context, Interests, Work Styles, Job Zones,
  Education, Technology Skills / Tools** — see "Deliberately not read".
- **Task Ratings.** `PERFORMS_TASK` carries `task_type` but no weights, though
  `Task Ratings.txt` publishes Importance / Relevance / Frequency per task.
  Weighted task edges would extend `score_paths` beyond skills.
- **Alternate-title provenance.** `Job Titles` and `Sample of Reported Titles`
  are merged into one `alt_labels` array, losing which file a title came from —
  which matters, because their evidential weight differs and the confidence
  policy currently treats them alike.
- **A performance benchmark.** The Locate shape follows PR #9's conclusion, but
  no A/B was measured on this graph; there is no "before" to measure against.
- **`ingestion/` extraction.** `db.py` is a near-copy of ESCO's. The shared
  version belongs in `ingestion/`, and moving ESCO's copy there is a change to
  another suite's code, so it is left for a PR that can touch both.
- **Crosswalks.** No cross-taxonomy links are created here; the official ESCO ↔
  O\*NET-SOC crosswalk is `crosswalks/`' work. Worth knowing when it lands: it
  reaches roughly 958 of the 1,016 O\*NET-SOC codes, so a few dozen occupations
  have *no* ESCO route by the official path — which is a recorded "no link",
  not a gap to fill by guessing.
