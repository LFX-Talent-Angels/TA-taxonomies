# BLS/SOC suite — build notes

**Taxonomy:** 2018 SOC · BLS Employment Projections 2024–34 (wages 2024) ·
**Graph DB:** Neo4j 5
**Suite package:** `src/ta_taxonomies/suites/bls/`

The ESCO and O\*NET suites are the template this follows, and `suites/onet/NOTES.md`
is the file to read next to this one — three of its findings are inherited here
rather than rediscovered, and they are cited where they are used. What is
written down below is mostly where **BLS differs from both**, because that is
what a reader coming from either will get wrong.

---

## What this suite is, and what it is not

ADR-0006 adopts BLS as *"the occupation spine and employment projections"*.
Both halves of that are load-bearing, and the second word of the first half is
the one that matters:

**There is no skills layer, and that is the point.** BLS publishes no
occupation→skill edge, so a caller asking this suite "what skills does X need"
is asking the wrong suite. `get_neighbors` refuses `HAS_SKILL` with
`unknown_rel_types` and a pointer to O\*NET or ESCO rather than returning the
empty list that an ESCO-shaped caller would read as *"this occupation needs no
skills"*. `validate_load` asserts the graph holds zero `HAS_SKILL` edges out of
a BLS node, because an invariant nobody enforces is a comment.

What it has instead, and what nothing else adopted has:

1. **The SOC spine itself.** ADR-0006 line 80 names the whole axis —
   `O*NET-SOC → SOC, BLS native SOC, ESCO → ISCO, ISCO ↔ SOC`. Every one of
   those arrows *ends on a SOC code*, and until this suite existed there was
   nothing at the other end: O\*NET derives `onet:soc:15-1252` from its own code
   prefix and says so in `suites/onet/ids.py` — *"the authoritative SOC nodes
   belong to the BLS suite"*. This is that suite.
2. **Numbers about the labour market rather than about a job's content.**
   Employment now, employment projected ten years out, annual openings, median
   wage, self-employed share, typical education — and the same figures spread
   across 420 industries.

### What this suite does that the other two cannot

Stated as capabilities, because "it has different data" is not an answer.

| Question | ESCO | O\*NET | here |
|---|---|---|---|
| What skills does this occupation need? | yes | yes, weighted | **no, by design** |
| Given a SOC code from any source, what is the authoritative occupation? | — | its own view of SOC | **yes** |
| Roll a detailed occupation up to broad / minor / major | — | — | **yes** |
| How many people do this job, and how many in ten years? | — | — | **yes** |
| Which industries employ it, and which of those are growing? | — | — | **yes** |
| Rank routes by where employment is *heading* | — | — | **yes** |

The row that answers the mentor's actual test — *does it make the axis explicit
and consultable rather than duplicating what already crosses?* — is the second.
The published ESCO ↔ O\*NET crosswalk already exists and is loaded. What it
hands you is an O\*NET-SOC code. `resolve_soc` is what turns that into an
authoritative node with employment, wages and a complete roll-up, and it
creates **no cross-suite edges** — the join is a string identity on the SOC
code, reported as `meta["join"] = "soc_code_string"` so it can never be mistaken
for a stored, cited crosswalk. Those stay `crosswalks/`' job.

```python
suite.resolve_soc("onet:occupation:29-1141.01")
#   detailed  bls:occupation:29-1141   Registered nurses
#   broad     bls:soc:29-1140          (no BLS title — derived)
#   minor     bls:soc:29-1000          Healthcare diagnosing or treating practitioners
#   major     bls:soc:29-0000          Healthcare practitioners and technical occupations
#   employment 3391.0k → 3557.1k (+4.9%), openings 189.1k/yr, median $93,600
#   warnings: ['onetsoc_extension_dropped']
```

And the case ARCHITECTURE.md cares about most:

```python
suite.resolve_soc("esco:occupation:f2b15a0e-…")
#   warnings: ['no_soc_code']
#   meta['next']: "crosswalks/ — a cross-taxonomy link is not this suite's to invent"
```

ESCO is ISCO-aligned and carries no SOC code. That is a **recorded "no link"**,
not a gap to fill by guessing.

---

## Is the download automatable without registration? Yes — with a caveat worth stating

**Verified 2026-08-25.**

| Host | What is there | Plain `curl` | Honest identifying User-Agent |
|---|---|---|---|
| `www.bls.gov/soc`, `/emp` (`.xlsx`) | the complete SOC structure workbook, the occupational projections workbook | **403** | **403** |
| `download.bls.gov/pub/time.series/` | the same programs' data as flat files | 403 | **200** |

`www.bls.gov` sits behind Akamai bot management. It refuses a plain request and
it refuses a request that identifies itself honestly; what gets through is a
full browser navigation header set (`Accept`, `Accept-Language`, `Sec-Fetch-*`,
`Upgrade-Insecure-Requests`). **This suite does not send one.** Pretending to be
Chrome to defeat a bot manager an agency deliberately put in front of its site
is not "automating a public-domain download", and the 403 page says in terms
that bot activity outside BLS usage policy is prohibited. That it *works* is
recorded here as a fact; it is not what the code does.

It does not need to. `download.bls.gov/pub/time.series/` is the flat-file server
BLS publishes **for** bulk retrieval, and it answers 200 to a User-Agent naming
the project and a contact address. So the answer is **yes: no account, no API
key, no click-through, no rate limit worth the name.** `fetch.py` sends that
User-Agent, refuses to run without `BLS_CONTACT`, and paces itself at one
request every two seconds.

**What the caveat costs, measured.** The flat files cover the SOC codes BLS
*publishes data for*, not the complete 2018 SOC:

- **825** detailed occupations
- **174** of the **450** broad groups its own detailed codes imply
- **95** minor groups, **23** major groups

The 276 missing broad groups are reconstructed from the codes that are present
(their *codes* are derivable; their *titles* are not), so roll-up is exact
everywhere and 276 nodes carry `title_source='derived'` to say what is missing.
The complete structure with a title for every group is in the workbook behind
the 403. If someone wants those titles, the honest routes are asking BLS for the
file or fetching it by hand — not a spoofed header.

### Files read

Twelve, all from `download.bls.gov` (~71.5 MB):

| File | Role |
|---|---|
| `ep/ep.occupation` | SOC code → title, matrix display level |
| `ep/ep.industry` | NEM industry code → title |
| `ep/ep.laytitle` | 5,820 everyday job titles → `alt_labels` |
| `ep/ep.series` | 113,473 occupation × industry cells + education/training codes |
| `ep/ep.data.1.AllData` | base-year employment per cell |
| `ep/ep.aspect` | 796,810 values: projections, openings, wages, shares |
| `ep/ep.eductrn`, `ep.otjt`, `ep.wkex` | code → text lookups |
| `ep/ep.dates` | base year / projection year / wage year |
| `oe/oe.occupation` | SOC **definitions** (830 of them) |

**Deliberately not read.** `oe.data.0.Current` (331 MB) and `oe.series` (1.2 GB)
are the OES wage series by area and industry — a whole geographic dimension this
suite does not model. `ep.aspect` is read in full because the occupation-level
wage, openings and self-employment figures are only in there.

---

## The finding that would have been silently wrong: the minor group is not derivable

This is the one worth reading if nothing else is.

SOC codes look like they carry their whole hierarchy in their digits, and
mostly they do:

| | derivable? | |
|---|---|---|
| major | **yes** | `15-1252` → `15-0000`, uniformly |
| broad | **yes** | `15-1252` → `15-1250`, uniformly |
| minor | **no** | see below |

**Minor groups come in two shapes and both are real.** 92 of the 95 BLS
publishes look like `NN-X000` (`29-1000` Healthcare Diagnosing or Treating
Practitioners); the other three look like `NN-XY00` (`15-1200` Computer
Occupations, `31-1100`, `51-5100`). So:

```
29-1210 Physicians          → minor 29-1000   (three zeros)
15-1250 Software developers → minor 15-1200   (two zeros)
```

There is not even a per-major rule to fall back on: majors **15, 31 and 51**
publish minor groups of *both* shapes.

The first version of this suite used "zero the last two digits", because the
first codes anyone looks at (`11-1000` Top executives) satisfy both readings and
hide the difference. Under that rule Registered nurses rolled up to `29-1100`,
a code SOC does not define; the derived-ancestor pass then *created a node for
it*; the load validated clean; every count added up; and every roll-up above the
broad group was one level wrong. The graph had **590 SOC groups instead of 575**
and nothing anywhere said so.

It was caught by a unit test on `soc_ancestors`, not by the loader, not by
validation, and not by reading. The lesson is narrower and more useful than "test
your code":

> **A hierarchy derived from an identifier is only as good as the exception you
> have not met yet. The majority shape is what hides the minority one — and
> "there are 92 examples that agree" is not evidence, it is the failure mode.**

The fix is that `resolve_soc_minor(code, published_minors)` takes the published
set as a **required** argument. There is no default that is right, so the
signature refuses to offer one.

---

## `ep.aspect` ships no code list, so the meanings are re-derived on every load

`ep/` publishes a lookup file for every coded column it has — `ep.eductrn`,
`ep.otjt`, `ep.wkex`, `ep.footnote` — and **none for `aspect_type`**, which keys
all 796,810 values. Reading those ten codes wrong would relabel every number in
the suite without breaking anything.

So they were established against the data and are re-checked at load time:

| Code | Meaning | How it was established |
|---|---|---|
| *(ep.data)* | employment, base year | `ep.dates.base_year` |
| `PR` | employment, projected year | `ep.dates.proj_year` |
| `A1` | employment change, numeric | `PR − base` on **113,473 of 113,473** rows, within published rounding |
| `A2` | employment change, percent | `A1 / base × 100` on **28,207 of 28,207** rows large enough for the identity to be about semantics rather than rounding |
| `A3` | percent self-employed | 62.9 Writers and authors · 17.7 Graphic designers · 0.5 Registered nurses · 0.1 Fast food workers — matching the published shares |
| `A4` | annual average openings | 115.2k Software developers · 189.1k Registered nurses — matching the published openings table |
| `A5` | median annual wage | \$133,080 Software developers · \$93,600 Registered nurses · \$30,480 Fast food workers — matching the published medians |
| `A6` / `A8` | industry share of the occupation, base / projected | 100 on total-all-industries series |
| `A7` / `A9` | occupation share of the industry, base / projected | 1.0 for Software developers economy-wide |

`A1` and `A2` are re-derived arithmetically **on every load**, and the loader
prints how many rows it checked — a guard that silently checks zero rows passes
for the wrong reason. `A3`/`A4`/`A5` are additionally asserted to appear *only*
on total-all-industries series: if BLS started publishing a median wage per
industry cell, this suite would keep reading the economy-wide one and be right by
luck, or read a cell's and be wrong with no warning.

**Why `A2` is only checked above 5.0 thousand jobs.** BLS computes the percent
from unrounded internals, so a cell of 1,200 jobs publishes `A1 = 0.0` beside
`A2 = 4.7` and both are correct. Below that threshold the check would be testing
rounding, not semantics. Above it, the identity holds on every qualifying cell.

---

## Real counts (Employment Projections 2024–34, full load)

Loaded 2026-08-25 into a throwaway Neo4j 5, `--mode full`.

| Nodes | | Relationships | |
|---|---|---|---|
| Detailed occupations | **825** | `EMPLOYED_IN` | **110,353** |
| Broad groups | **457** | `BROADER_THAN` | **1,377** |
| Minor groups | **95** | | |
| Major groups | **23** | | |
| Industries | **420** | | |
| **Total** | **1,820** | **Total** | **111,730** |

Every minor and major group here is published by BLS; only broad groups needed
reconstructing.

Spine composition: **1,124** published codes + **276** reconstructed broad
groups = **1,400** SOC nodes. Every node that is not a major group has exactly
one `BROADER_THAN` edge (1,400 − 23 = 1,377), and **no edge skips a level** —
which is checked, because a skip is the only symptom the reconstruction pass
would show if it dropped a group.

Other measured facts:

- **6,828 lay titles** over **1,082** nodes, from `ep.laytitle` plus the OES
  title where it differs from the EP one.
- **830 SOC definitions**, from `oe.occupation`. Public domain, so unlike SFIA's
  descriptive text these are stored rather than fetched at runtime.
- **833 nodes carry a median wage** — the 825 detailed occupations plus 8
  summary lines BLS publishes wage and openings figures for (`00-0000 Total, all
  occupations`, `13-1020 Buyers and purchasing agents`, and six others).
- Every detailed occupation has a wage and a typical-education level. No holes.
- Projected change spans **+49.9%** (Wind turbine service technicians) to
  **−36.1%** (Word processors and typists) across all 1,113 occupation totals.

### Facts that break ESCO- and O\*NET-shaped assumptions

- **276 broad groups have no title at all.** They exist because a detailed code
  implies them and BLS publishes no line for them. `validate_load` requires that
  every untitled node be exactly one of these; it does *not* require a title
  everywhere, which would refuse to load correct data.
- **The employment matrix overlaps by design.** `occ_type`/`ind_type` are `L`
  (a detail line) or `S` (a summary containing others), and both are published.
  33,786 of the 110,353 cells are detail×detail. **Summing employment over every
  edge double-counts**, so `get_neighbors` returns
  `meta["employment_matrix"]["overlapping"] = True` and names the two properties
  to filter on. A caller who does not know this gets a plausible wrong number,
  which is worse than an error.
- **`TE1100` and `TE1200` are not industries.** They are "Self-employed workers"
  and "Total wage and salary employment" — class-of-worker splits. Loading them
  as `:BlsIndustry` would put "Self-employed workers" beside "Crop production" in
  every answer to "which industries employ X". They land on the occupation as
  properties instead.
- **Five industry titles are shared by two nodes each.** `540000` and `541000`
  are both "Professional, scientific, and technical services" at different matrix
  levels. Locate reports `ambiguous`; this is the source, not a load bug.
- **The EP and OES programs disagree on 3 of 1,093 shared titles** and on the
  casing of ~1,005 more. EP wins (it is what every projection figure is published
  under) and the OES form is kept as an alias — someone who typed it typed a real
  BLS title.

---

## Where the shared contract met BLS's shape

Four places, in descending order of how much they cost.

### 1. The canonical vocabulary has no kind for the economic dimension

ARCHITECTURE.md's node vocabulary is `Skill · Task · Occupation · Framework ·
Level · Evidence`, and its edge vocabulary is `HAS_SKILL · PERFORMS_TASK ·
BROADER_THAN · RELATED_TO · HAS_LEVEL · SUPPORTED_BY · MAY_LEAD_TO`. **An
industry is none of those, and "this many people in this occupation work in this
industry" is none of those either.**

That is not a small gap for the suite ADR-0006 adopts *for* its economic data.
Borrowing the nearest label would put 420 BLS industries into whatever a
cross-suite reader means by `:Framework`, and putting the employment matrix on
`RELATED_TO` would keep the edges and throw away the only thing they say.

**What this suite does:** industries carry `:BlsNode` + `:BlsIndustry` and **no
canonical label**, announcing their kind through `Node.kind`, which the contract
documents as *"Canonical **or suite** kind"*. The edge is `EMPLOYED_IN`,
suite-declared and traversable. **What it does not do:** file a contract PR
adding `Industry` and `EMPLOYED_IN` to the shared vocabulary. That is a code-owner
change that ripples into `TA-agents`, and it should be decided rather than
slipped in by the third suite to need it. It is the change worth proposing next.

The consequence today is that `EMPLOYED_IN` is unreachable through TA-agents,
which only ever asks for `HAS_SKILL`. That is the same caller limitation O\*NET
recorded for `PERFORMS_TASK` and `RELATED_TO`, one suite later and on the edge
that carries this suite's whole payload.

### 2. `relation_type` — the ESCO concept O*NET had to project. Here there is nothing to project it from

`TA-agents/.../reveal.py` filters neighbours on
`edge.properties["relation_type"] == request.relation_kind`, defaulting to
`"essential"`. O\*NET solved this by projecting Importance onto ESCO's binary
under a declared policy. **This suite cannot**, because there is no skill edge
and no rating to project — the honest answer is that the question does not apply.

So `get_neighbors` refuses `HAS_SKILL` outright rather than returning an empty
list. That is a *different* answer from "no skills found", and the difference is
the whole point: an empty list is valid, so nothing anywhere would flag it, and
the agent would report that Registered nurses need no skills.

### 3. `Candidate.confidence` is a bare float, and now three suites are on three scales

`contract/models.py:143` is a `float` in `[0, 1]` with no room to say what it
measures. ESCO's numbers, O\*NET's and this suite's are **not comparable**, and
the reason here is different again from O\*NET's granularity argument:

- A SOC code is an *identity BLS assigns*, so an exact code hit is as certain as
  this suite gets — `CONF_EXACT_CODE = 0.99`, above every title tier.
- The alias pool is 5,820 lay titles over 817 occupations, published by BLS
  itself as the everyday names for a SOC line. That is curated, unlike O\*NET's
  deliberately idiosyncratic 57k, so an exact alias hit is *stronger* evidence
  here (0.88) than there (0.82) — for a structural reason, not tuning.
- A hit on a SOC code carried inside another suite's identifier is reported under
  its own method (`foreign_soc_code`) at its own confidence (0.97), because it is
  a string identity rather than a hit on this suite's own code and a caller
  should be able to tell.

Every result names `meta["confidence_policy"]`. O\*NET does the same; ESCO still
returns no marker, so **sorting a three-suite candidate list by `confidence` is
a bug waiting to be written.** Fixing it properly means a calibration that maps
all three onto one scale, which is a decision to be taken, not slipped in here.

### 4. `SuiteName` already had `"bls"` — a non-problem worth recording

`contract/models.py:17` pins `SuiteName` to five values and `"bls"` is one of
them. Nothing needed changing. Recorded because it is the sort of thing a fourth
suite might otherwise have filed a contract PR for.

---

## Coexistence: what a shared graph found that an empty one cannot

`suites/onet/NOTES.md` states the rule this suite was built to satisfy:

> A load against an empty graph proves the loader *writes*. It does not prove
> the loader *coexists*.

So the full load was run into a Neo4j that already held the ESCO fixture, and the
impostor case was planted deliberately. Three results, all measured:

**1. The label-set MERGE bug is guarded, and the guard fires.** A crosswalk that
materialises an endpoint before its suite is loaded leaves a node with the right
id and none of this suite's labels. MERGE keys on `:BlsNode`, so it does not
match — it creates a second node:

```
planted:  (:CrosswalkStub {id:'bls:occupation:15-1252'})
load:     merges 4 occupations, 9 groups, 33 industries, 110 edges — all counts correct
validate: 1 node(s) carry a 'bls:' id without the :BlsNode label …
after:    2 nodes hold bls:occupation:15-1252
```

The uniqueness constraint cannot see it (constraints are per label) and every
count still adds up (the count queries are label-scoped too). Without the check
the identity rule the whole graph rests on is quietly false.

**2. A second variant is blocked by ESCO's constraint, not by this suite's —
which is worth knowing.** Planting `(:Occupation {id:'bls:occupation:15-1252'})`
instead does not reach validation at all; it fails at `CREATE` with

```
Neo.ClientError.Schema.ConstraintValidationFailed:
Node(717) already exists with label `Occupation` and property `id` = 'bls:occupation:15-1252'
```

because ESCO declares uniqueness on the **bare** `:Occupation(id)` label and this
suite's occupations carry that canonical label. Two things follow. It is
*useful* — a whole class of impostor cannot be created once BLS is loaded. It is
also *fragile*: the protection belongs to a schema object this suite does not
own, on a label it does not control, and only applies because the BLS node was
loaded first. `DROP CONSTRAINT esco_occupation_id` would remove it silently.
This suite's own `bls_blsoccupation_id` is what it actually relies on.

**3. The reverse of O\*NET's constraint finding.** O\*NET measured that a second
uniqueness constraint on a label another suite constrains is a **silent no-op**
under `IF NOT EXISTS`. Confirmed from the other side here: after loading both
suites, `SHOW CONSTRAINTS` lists `esco_occupation_id` on `:Occupation` and
`bls_blsoccupation_id` on `:BlsOccupation` — and every `bls_*` object names a
`Bls`-prefixed label. That is asserted in a test against the live database, not
just against the statement strings.

---

## Locate and traversal: inherited, not rediscovered

Both shapes come from what the O\*NET suite measured, and neither was
re-derived here:

- **Exact tiers seek concrete labels**, unioned; alias / case-insensitive /
  substring tiers retrieve through a full-text index (`bls_node_text`,
  `standard-no-stop-words`). PR #9 measured what matching an umbrella label and
  filtering with `labels(n)` costs — the planner cannot reach a per-label index
  from there. The index only *retrieves*; each tier re-applies its predicate in
  Cypher, so a Lucene relevance score never becomes a confidence value.
- **A SOC-code tier runs first**, and unlike O\*NET's it accepts codes carried
  inside another suite's identifier.
- **The branching cap is per relationship type**, and **a hop that lands on the
  target is exempt from it**. The second matters more here than it did there: a
  summary industry such as "Professional, scientific, and technical services" is
  reached by most of the 1,113 occupations, so its fifteen largest neighbours are
  the fifteen occupations that fill it and almost never the one being asked about.

Expansion is ordered by descending `industry_share_of_occupation_base`, so when
the cap bites it keeps the hops where most of the jobs are — a published number,
not a tie-break invented to make the cut deterministic.

---

## `score_paths`: three policies, and what they buy

All three are backed by published BLS figures. What is *policy* is how a column
of percentages becomes one number per path.

| Policy | Rule |
|---|---|
| `bls-employment-share-bottleneck/1` | minimum industry share of the occupation's employment along the path |
| `bls-employment-share-mean/1` | arithmetic mean of the same |
| `bls-projected-growth/1` | minimum projected percent change, mapped onto `[0,1]` over `GROWTH_WINDOW_PCT` |

The third is the one that could not exist without this source. Measured on the
full graph, ranking the routes between Software developers (`15-1252`) and
Registered nurses (`29-1141`):

```
bls-employment-share-bottleneck  Finance and insurance, Insurance carriers …, Management of companies
bls-employment-share-mean        Professional scientific and technical services, …, Computer systems design
bls-projected-growth             Information, Computer systems design …, Insurance carriers …
```

**All three disagree from rank one**, which is a stronger divergence than
O\*NET's three policies show, and it is the honest reason to keep them separate:
where the jobs are and where they are going are different questions.

Reproduce it against a graph loaded with `--mode full`:

```python
from ta_taxonomies.contract import PolicyRef
from ta_taxonomies.suites.bls.db import neo4j_driver
from ta_taxonomies.suites.bls.tools import BlsSuite

with neo4j_driver() as (driver, database):
    suite = BlsSuite(driver, database=database)
    found = suite.enumerate_paths(
        "bls:occupation:15-1252", "bls:occupation:29-1141", max_depth=2, max_paths=20
    )
    labels = {n.id: (n.label or n.id) for n in found.nodes}
    for name in (
        "bls-employment-share-bottleneck",
        "bls-employment-share-mean",
        "bls-projected-growth",
    ):
        result = suite.score_paths(found.paths, PolicyRef(name=name, version="1"))
        print(name, [labels[s.path.node_ids[1]] for s in result.scored_paths[:3]])
```

Declared, and not source data:

- **`GROWTH_WINDOW_PCT = 50.0`.** Percent change is unbounded above and floored
  at −100, so scoring needs a window and BLS does not supply one. ±50% maps
  linearly onto `[0,1]`, so no change scores 0.5. Chosen for being explicable,
  then measured: **all 1,113 occupation totals fall inside it**, and **260 of the
  110,353 industry cells (0.2%) fall outside**, up to +208.9%. Those clamp, and
  the count comes back in the result's warnings so a clamped value never passes
  for a measured one.
- **`UNWEIGHTED_EDGE_SCORE = 0.5`** on every `BROADER_THAN` hop. SOC's tree is
  structure, not measurement; the neutral midpoint neither rewards nor punishes
  rolling up, and any other value would be a claim BLS does not make.
- An unknown policy name, or a known name at an unknown version, is **refused**.

---

## Things that were nearly wrong

Kept because they are the useful part of the file.

**`levels_skipped` was decorative until it was made load-bearing.** The property
records how many SOC levels a `BROADER_THAN` edge jumps, and on a full load it is
`0` on all 1,377 edges — necessarily, because the reconstruction pass materialises
every ancestor. A property that is provably constant is normally dead weight. It
is kept, and now *asserted* to be zero, because it is the cheapest possible check
that the reconstruction ran: if the pass ever drops a group, the load does **not**
fail on a missing endpoint — the edge simply lands one level higher, and every
count still adds up. This is the only thing that would notice.

**The Neo4j notification that looks like an error is the check working.** Every
load prints a `01N51` warning that the relationship type `HAS_SKILL` does not
exist. That is `validate_load` asserting the absence of a skills layer against a
graph that correctly has none. It is noise, and suppressing it would mean
suppressing a class of warning worth seeing.

**`tests/*/__init__.py` had to be added, including under `suites/esco/`.**
Without them pytest cannot collect two suites' tests together — `test_ids.py`
exists in both and the second import fails with a file mismatch. They are empty
marker files, they touch no ESCO test code, and the `feature/onet-suite` branch
already adds the same ones. Flagged rather than done quietly, because it is a
directory this suite does not own.

---

## Reproduce

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# A throwaway Neo4j. A load DELETES this suite's nodes, so never point it at a
# graph you care about.
docker run -d --name ta-neo4j-bls -p 7480:7474 -p 7694:7687 \
    -e NEO4J_AUTH=neo4j/bls-dev-password neo4j:5-community

export BLS_NEO4J_URI=bolt://localhost:7694
export BLS_NEO4J_PASSWORD=bls-dev-password
export BLS_CONTACT="you@example.org"      # fetch.py refuses to run without it

python -m ta_taxonomies.suites.bls.fetch --out data/bls/raw     # 12 files, ~71.5 MB
python -m ta_taxonomies.suites.bls.load  --mode fixture
python -m ta_taxonomies.suites.bls.load  --mode full --data-dir data/bls/raw

pytest tests/suites/bls -q
```

`BLS_NEO4J_*` overrides `NEO4J_*` for exactly one reason: every suite reads the
same `NEO4J_URI`, and one stale export is enough to point a wiping loader at the
graph someone else was using. The resolved URI is printed *before* anything is
deleted.

To exercise the coexistence path the way it was tested, load another suite first:

```bash
NEO4J_URI=bolt://localhost:7694 NEO4J_PASSWORD=bls-dev-password \
    python -m ta_taxonomies.suites.esco.load --mode fixture
python -m ta_taxonomies.suites.bls.load --mode full --data-dir data/bls/raw
```

Tests that need Neo4j carry `pytest.mark.neo4j` and skip when it is absent; they
reload the fixture with `wipe=True`, so re-run `--mode full` afterwards if you
wanted the complete graph.

---

## Not done

- **A contract PR for `Industry` / `EMPLOYED_IN`.** The gap is documented above
  and is the next thing worth proposing; it needs a code owner because it ripples
  into `TA-agents`.
- **Crosswalk edges of any kind.** `resolve_soc` is a lookup on a code string and
  says so. `onet:soc:X ↔ bls:soc:X` and `ISCO ↔ SOC` are stored, cited links and
  belong in `crosswalks/`. This suite deliberately creates none — including the
  one that would be trivially easy.
- **The complete SOC 2018 structure.** 276 broad groups have reconstructed codes
  and no titles, and detailed codes BLS publishes no data for are absent
  entirely — 825 are here, against the 867 distinct SOC codes `suites/onet/NOTES.md`
  records O\*NET as covering, so roughly forty. Both gaps are consequences of not
  spoofing `www.bls.gov`, and both are countable rather than vague. The
  authoritative total is in the workbook behind the 403 and is not asserted here
  from a source that was not read.
- **OES wages by area or industry.** A whole geographic dimension, 1.5 GB of it,
  and no node kind here to hold a place.
- **Industry hierarchy.** `ep.industry` carries a `display_level` and NAICS-ish
  codes, so a `BROADER_THAN` tree over industries is derivable for the numeric
  codes — but not for `1131-2`, `3250A1`, `61110L`. A tree that covers most codes
  and silently omits the rest is worse than none, so industries are flat.
- **A performance benchmark.** The Locate shape follows PR #9's conclusion, but
  no A/B was measured on this graph; there is no "before".
- **`ingestion/` extraction.** `db.py` is now the *third* near-copy of the same
  file. The shared version belongs in `ingestion/`, and moving all three is a PR
  that can touch all three suites.
- **Seasonally adjusted or historical series.** EP publishes one annual
  observation per series; there is no time dimension here to model.
