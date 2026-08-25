# SFIA suite — build notes

**Taxonomy:** SFIA 9 · **Graph DB:** Neo4j 5
**Suite package:** `src/ta_taxonomies/suites/sfia/`

The ESCO suite is the template and the O\*NET suite is the nearest sibling. What
is written down here is mostly where SFIA **differs** from both, because that is
what a reader arriving from either will get wrong.

---

## The licence decides the design, so it comes first

SFIA's free licence covers personal and internal use. A fee-bearing licence is
required for *"redistributing SFIA material in electronic or printed form to any
other organisation (even if affiliated)"*, and the same page asks users not to
*"copy content from the SFIA website … and include it on your own websites"*
([licensing](https://sfia-online.org/en/about-sfia/licensing-sfia), checked
2026-08-25). A public Apache-2.0 repository is redistribution to everyone.

TA-workspace **ADR-0006 §2** therefore adopts SFIA *for structure only*. This
suite stores:

| Loaded | Not loaded, and why |
|---|---|
| Skill code (`PROG`) | The skill's description — licensed prose |
| Skill name | The definition of the skill at each level — licensed prose |
| Level numbers 1–7 | The "essence of the level" — licensed prose |
| Which levels each skill is defined at | Guidance notes — licensed prose |
| Published related-skill links | SFIA's **names** for its levels — see below |
| Category / subcategory when the source has them | The names of its five generic attributes — see below |

**Two deliberate abstentions.** ADR-0006 §2 authorises "level numbers". The
seven level *names* and the five generic *attribute* names are short, but they
are SFIA's wording rather than a number or a code, and the rule this suite works
under is that an uncertain case is left out and asked about rather than loaded.
A level's label here is `"Level 4"`. Both are one line to add if the answer comes
back the other way.

The names themselves appear exactly once in this repository — as a denylist in
`test_level_names_are_not_stored`, the same status as the words in
`LICENSED_FIELD_MARKERS`. Naming them in prose here to explain the decision would
have been storing the thing the decision says not to store, so the rest of these
files refer to them without spelling them out.

### Enforced, not intended

A comment saying "don't add descriptions" is an assertion with no `assert`. Three
mechanisms, deliberately overlapping, because each catches what the others cannot:

1. **An allowlist.** `ALLOWED_NODE_KEYS` in `config.py` is the complete set of
   keys a node row may carry. Adding a licensed field is therefore not something
   that happens while threading one more value through `normalize`; it requires
   editing a tuple that has this reasoning attached to it.
2. **A name check**, applied recursively — including inside `extra`, which the
   allowlist would otherwise wave through as a dict. `level_description`,
   `essenceOfTheLevel` and `guidance_notes` are all refused by their stem.
3. **A length ceiling** (`MAX_LABEL_CHARS = 120`). The first two ask *where* text
   was put; only this one asks *what it is*. The longest SFIA 9 skill name is 44
   characters. A sentence of SFIA prose is not.

All three run in `assert_factual_only`, which `load.py` calls before the first
MERGE — and `validate_load` then re-runs the last two **against the graph**,
because the payload check cannot see a property written by anything else, and the
graph is what gets published.

The extractor reads the licensed text and drops it at the point of reading, with
the reason at each site. `extract_skill_page` is the clearest case: SFIA renders
all seven level sections on every skill page and fills in only the ones where the
skill is defined, so the *presence* of content is the structural fact. That is
reduced to a boolean while the page is being read; what leaves the function is a
list of integers.

---

## Why this suite, and what it unlocks

SFIA has **no occupations**. It cannot answer "what skills does a data engineer
need" — that is ESCO's and O\*NET's question, and no amount of work here will
make it this suite's. Saying so plainly matters more than it sounds: the
commonest call in TA-agents is exactly that question, and a suite that quietly
answers it badly is worse than one that says it cannot.

What SFIA has, and neither other source does, is a **responsibility axis**. ESCO
is structurally binary (essential / optional, no field for a number). O\*NET's
numbers are survey-measured *importance and level of a skill to an occupation*.
Neither says how much responsibility a person carries when exercising a skill.
SFIA does, on a published 1–7 ordinal, per skill.

Questions that becomes answerable, and were not before:

- **"How senior is this?"** — `PROG` is defined at 2–6; `ISCO` only at 6–7.
  Two skills someone might list side by side sit in different halves of a career.
- **"Where does a career in this skill run out?"** — 21 skills stop at level 5
  and 35 reach level 7. A skill's ceiling is a fact about the work, not about the
  person doing it.
- **"What can someone start with?"** — 16 skills are defined at level 1. That is
  a published list of entry points into the digital profession.
- **"Is this a promotion or a sideways move?"** — same skill at a higher level is
  progression; a different skill at the same level is a lateral move. Only a
  responsibility axis distinguishes them, and the `MAY_LEAD_TO` ladder between
  levels makes the first traversable.
- **"Who could plausibly work together?"** — two skills that share a level are
  practised at comparable responsibility. That is the connective tissue this
  graph has instead of occupations, and it is what `enumerate_paths` walks.
- **"Is this framework's level 4 the same as ours?"** — SFIA's own material
  presents the levels as a mapping surface for other frameworks. That makes the
  level nodes the natural anchor for a future crosswalk, in a way skill codes are
  not.

And a caution that belongs in the same list: a level is **not** a weight. A
level-7 edge does not mean "this skill matters more"; it means the skill is
defined for someone setting strategy. Sorting a skill list by level answers
"how senior", never "how important".

---

## Data source

| Item | Detail |
|------|--------|
| **Version** | SFIA 9 (SFIA 10 is in public consultation; it is not this) |
| **Source** | <https://sfia-online.org/en/sfia-9> |
| **Bulk download** | **None public.** The spreadsheet distribution is behind registration. |
| **Licence** | Free for personal / internal use; redistribution is fee-bearing |

Because there is no public bulk file, `--mode full` reads a **local snapshot**
that the contributor fetches under their own licence (`fetch.py`, one request
every 0.4 s, identifying the project in its User-Agent). It lands in
`data/sfia/` — gitignored, never committed. A licensee holding the spreadsheet
can instead drop a `structure.csv` in the same directory; the loader prefers it,
and any column outside the documented five is ignored rather than loaded, since
the export step is where a user is most likely to hand over more than this
repository may keep.

**The public pages carry no category tree.** SFIA's category / subcategory view
is behind a login. So a snapshot-built load has skills, levels and related-skill
links and *no* groups — which is stated in the document's meta and printed at
load time, and is why `validate_load` does not require categories. The
`BROADER_THAN` path is exercised in tests with obviously synthetic group names
rather than by guessing SFIA's real assignment, because inventing structure is
the same class of mistake as copying prose: putting something in the graph the
source never said.

---

## Graph model

```text
(:SfiaSkill)-[:HAS_LEVEL {level}]->(:SfiaLevel)
(:SfiaLevel)-[:MAY_LEAD_TO {from_level, to_level}]->(:SfiaLevel)
(:SfiaSkill)-[:RELATED_TO]->(:SfiaSkill)
(:SfiaSkill|:SfiaSubcategory)-[:BROADER_THAN]->(:SfiaSubcategory|:SfiaCategory)
```

Every node carries three labels — `:SfiaNode`, a suite-scoped concrete label, and
the canonical `ARCHITECTURE.md` one.

**Levels are materialised, not derived.** All seven exist on every load, whatever
slice was loaded. Deriving them from the skills present would make the
responsibility axis depend on the slice — and the axis is the entire reason this
suite exists. The fixture's skills happen to cover 1–7, which is precisely why
this has to be a rule rather than a coincidence.

**The ladder is published structure, not inference.** SFIA defines its levels as
a progression, so `MAY_LEAD_TO` links each level to the next one up. Only
adjacent steps: a level-2-to-level-6 edge would assert a jump nobody claims, and
a path query can still walk the rungs.

**`RELATED_TO` is published too**, and it is stored **as published rather than
symmetrised**: 1,058 of the 1,121 links are reciprocated and 63 are one-way.
Making the graph symmetric would invent 63 edges SFIA did not publish, which is
cheap to do and impossible to distinguish afterwards.

### Identity: four bare letters, and the trap ADR-0006 names

ESCO's identity is a URI that names its own type. O\*NET's is a code whose
structure carries meaning. SFIA's is **four letters with no namespace at all**:

> `ISCO` in SFIA is *Information systems coordination*. `ISCO` in ESCO's world is
> the ILO's occupation classification. Four identical characters, nothing else in
> common.

So the bare code can never be an identity in this graph — only `sfia:skill:ISCO`
is — and every node carries `source` and `source_id` so the provenance survives
being copied somewhere it should not be. `search_nodes` returns
`meta["code_scope"] = "sfia-skill-code"` on any code hit, so an answer says which
namespace produced it rather than leaving the caller to assume.

Category and subcategory ids are **derived from their names**, because SFIA
publishes no identifier for them. That makes those ids sensitive to a rename
between versions; the `version` property on every node is what tells two
generations apart. Subcategory ids are scoped by their category, because a
subcategory name is only unique *within* one — an unscoped slug would merge two
different groups into one node with every count still adding up.

---

## Where the shared contract met SFIA

The contract's *shapes* are source-agnostic. Its *semantics*, and the code
calling it, are not. Three places, in descending order of how much they hurt.

### 1. `relation_type` — and here the O\*NET fix is the wrong fix

`TA-agents/src/talent_angels/skills/connect/reveal.py:94` keeps only neighbours
whose `edge.properties["relation_type"]` equals the requested kind, and the
phrase router defaults that kind to `"essential"`. Against a SFIA graph that
filter matches nothing and the agent reports *no skills at all* — silently,
because an empty list is a valid answer.

The O\*NET suite solves this by projecting its published Importance onto the
essential/optional binary. **This suite must not, and that is the interesting
part.** O\*NET had a published number to be lossy about. SFIA has no
occupation-to-skill edge at all, and its levels measure responsibility rather
than how essential a skill is to a job: a level-7 `HAS_LEVEL` edge does not mean
"essential", it means the skill is defined for someone setting strategy. A
projection here would not be lossy, it would be **invented** — the move ADR-0006
§3 records for ESCO and forbids.

**What this suite does instead:** makes the absence visible. `RELATION_TYPE_POLICY`
is named `sfia-no-essential-projection`, `get_neighbors` returns it in `meta` and
attaches a `relation_type_absent` warning to every result carrying edges, and
`validate_load` **asserts that no edge carries `relation_type`** — an assertion
that a value is *absent*, which is unusual and deliberate, because the tempting
shortcut is exactly to add one.

The proper fix is for `relation_kind` to be a suite-interpreted concept rather
than a literal property comparison, and that is a change to another repo.

### 2. There is no alias tier, because there are no aliases

`Candidate.confidence` is a bare float with no room to say what it measures, and
this suite's numbers are not comparable with ESCO's or O\*NET's — the O\*NET
NOTES makes that case and it holds here for a further structural reason:

- **Granularity.** SFIA describes the whole digital profession in **147** skills.
  ESCO uses 13,939. The same "0.95" resolves to a far coarser thing.
- **No alias pool at all.** ESCO ships curated `altLabels`; O\*NET ships 57k lay
  job titles. SFIA publishes one name per skill. So Locate here has **no
  `exact_alt` tier** — not a lower-confidence one, none — and a user's own
  wording reaches the substring tier or nothing. `search_nodes("coder")` returns
  `not_found`, and that is the correct answer: any synonym list this suite
  matched against would be one we wrote.

Nodes therefore carry no `alt_labels` property either. An always-empty list would
invite a tier that can never match.

**One tier ESCO does not have:** an exact match on the four-letter code, tried
first. "PROG level 4" is how SFIA is spoken, so the code is an identity a user
actually types. It is worth being precise about the cost: the screen is
syntactic, so a four-letter English word is screened in and costs one indexed
seek that finds nothing before the name tiers run. That is the right trade in a
vocabulary where `TEST` (*Functional testing*) and `PORT` (*Software
configuration*) really are codes — put the code tier last and typing one returns
every skill whose name happens to contain those four letters, with the identity
the user typed somewhere down the list.

### 3. `Level` is canonical vocabulary that nothing calls

`ARCHITECTURE.md` lists `Level` as a node kind and `HAS_LEVEL` / `MAY_LEAD_TO` as
edge types, and this suite maps onto them exactly. But no TA-agents call site
asks for `HAS_LEVEL`, so **the responsibility axis is unreachable through the
agent today** — the same shape as O\*NET's `PERFORMS_TASK`, and worse in effect,
because for this suite the unreachable part is not a side channel: it is the
whole contribution. That is a caller limitation rather than a defect here, but it
is the single most important thing to fix downstream, and nothing in this repo
can fix it.

`SuiteName` already pins `"sfia"`, so no contract PR was needed.

---

## Real counts (SFIA 9, full load)

Loaded 2026-08-25 into a throwaway Neo4j 5 on port 7695, `--mode full`.

| Nodes | | Relationships | |
|---|---|---|---|
| Skills | **147** | `RELATED_TO` | **1,121** |
| Levels | **7** | `HAS_LEVEL` | **672** |
| Categories | **0** (not public) | `MAY_LEAD_TO` | **6** |
| Subcategories | **0** (not public) | `BROADER_THAN` | **0** |
| **Total** | **154** | **Total** | **1,799** |

Skills defined at each level:

| Level | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| Skills | 16 | 99 | 113 | 139 | 144 | 126 | 35 |

How wide a skill's band is, which is the shape of the framework's own claim that
skills are deliberately **not** defined at all seven levels:

| Levels per skill | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|
| Skills | 3 | 23 | 33 | 63 | 25 | **0** |

Facts worth having in front of you:

- **No skill spans all seven levels.** Not one of the 147. The framework's
  "skills are not defined at all 7 levels" is not a caveat, it is the shape of
  every row.
- **Every band is contiguous.** No skill is defined at, say, 2 and 5 but not 3–4,
  so `min_level`–`max_level` is a faithful summary and the loader stores it.
- **No skill is defined at both level 1 and level 7.** The entry-level 16 and the
  strategy-setting 35 are disjoint sets.
- **The three narrowest skills** — two levels each — are *Governance*,
  *Programme management*, and *Information systems coordination* (`ISCO`, the
  code ADR-0006 warns about, which exists only at 6–7).
- **Every skill reaches at least one level**, so `validate_load` asserts it.
  Contrast O\*NET, where 122 of 1,016 occupations carry no ratings and the
  equivalent assertion would refuse correct data.
- **Levels are extreme hubs.** Seven nodes carry all 672 `HAS_LEVEL` edges; level
  5 alone has degree 146. That is O\*NET's descriptor-element problem again, at a
  smaller absolute scale but a worse ratio, and it forced the same two traversal
  rules.

---

## Traversal: the two O*NET rules, needed again for the same reason

Both were kept because the shape demands them, and the integration tests fail
without the second.

**1. The branching cap is applied per relationship type.** SFIA has no published
edge strength to order by, so under one shared cap the ordering would decide
which whole relationship *types* survive rather than which edges within a type
do.

**2. A hop that lands on the target is exempt from the cap.** A level's fifteen
kept neighbours are fifteen of its ~140 skills and almost never the one being
asked about, so without this the cap hides the answer instead of bounding the
search — returning nothing while reporting a large, healthy-looking `pruned`
count.

**What is *not* like O\*NET:** the ordering carries no meaning. O\*NET keeps its
strongest edges because Importance is a published strength. Here the tie-break is
by level where there is one and by node id otherwise — deterministic, and no more
than that. The arrival exemption is what makes Pathfind work in this suite, not
the ordering. Saying that plainly matters, because the ordering *looks* like
O\*NET's and does a different amount of work.

---

## `score_paths`: implemented, and honest about what it buys

Three named, versioned policies, all normalising the published 1–7 scale to
`[0, 1]`:

| Policy | Rule |
|---|---|
| `sfia-level-bottleneck/1` | minimum along the path — as senior as its least senior rung |
| `sfia-level-peak/1` | maximum — how far up the route reaches |
| `sfia-level-mean/1` | arithmetic mean — the route's overall altitude |

The numbers are source data; **the ranking rule is ours**, which is why they are
named and an unknown policy is refused rather than approximated. Read a score as
"how senior is this route", never "how strong is this match".

Measured on the full graph, ranking routes between `USUP` (*Incident management*,
levels 1–5) and `ITSP` (*Strategic planning*, levels 4–7), showing the levels each
route passes through:

```
sfia-level-peak        Level 6 → Level 7 (1.00)   Level 5 → Level 6 (0.83)   Level 6 → Level 5 (0.83)
sfia-level-bottleneck  Level 6 → Level 7 (0.83)   Level 6          (0.83)   Level 5 → Level 6 (0.67)
sfia-level-mean        Level 6 → Level 7 (0.94)   Level 6          (0.83)   Level 5 → Level 6 (0.78)
```

All three agree on the strongest route and diverge from rank two — the direct
single-level hop is second under bottleneck and mean and does not place under
peak, which is the disagreement these policies exist to express. Reproduce it
against a graph loaded with `--mode full`:

```python
from ta_taxonomies.contract import PolicyRef
from ta_taxonomies.suites.sfia.db import neo4j_driver
from ta_taxonomies.suites.sfia.tools import SfiaSuite

with neo4j_driver() as (driver, database):
    suite = SfiaSuite(driver, database=database)
    found = suite.enumerate_paths("sfia:skill:USUP", "sfia:skill:ITSP", max_depth=4, max_paths=20)
    labels = {n.id: n.label for n in found.nodes}
    for name in ("sfia-level-peak", "sfia-level-bottleneck", "sfia-level-mean"):
        result = suite.score_paths(found.paths, PolicyRef(name=name, version="1"))
        print(
            name,
            [" → ".join(labels[i] for i in s.path.node_ids[1:-1]) for s in result.scored_paths[:3]],
        )
```

**Writing that snippet is what found a bug.** Path expansion is undirected, as in
the other suites, so `MAY_LEAD_TO` — stored in one direction — can be walked
*downward*. It was being scored by its `to_level` regardless, so a route stepping
**down** to level 5 scored as if it had reached level 6. `_edge_score` now takes
the node the hop arrives at. The effect is visible under the mean policy (the
descending route drops from 0.78 to 0.72 and out of the top three) and invisible
under min and max, because a ladder hop always sits next to a `HAS_LEVEL` edge at
the same rung. A claim about ranking with no way to re-run it is a claim the
reader has to take on trust; this is the second time in this repo that writing the
reproduction is what caught the error.

Declared, and not source data:

- A hop with no level (`BROADER_THAN`, `RELATED_TO`) contributes
  `UNWEIGHTED_EDGE_SCORE = 0.5`, with a caveat the O\*NET suite does not have:
  on a 1–7 scale normalised to `[0, 1]`, **0.5 is exactly level 4**. A structural
  hop therefore scores like a level-4 one. Every other candidate value says
  something worse — 0.0 reads as "measured and found worthless", and dropping the
  hop would shorten the evidence and *raise* a bottleneck score — so the number
  stays and the count of hops it applied to is reported in the warnings.
- An unknown policy name, or a known name at an unknown version, is refused.

---

## Labels and constraints: measured, not assumed

Nodes carry `:SfiaNode` (umbrella, one unique-`id` constraint), a suite-scoped
concrete label (`:SfiaSkill` — what search matches on), and the canonical label
(`:Skill`, `:Level` — for cross-suite reads).

The prefix on the concrete label is not because Neo4j would refuse a second
constraint. Measured on this machine against Neo4j 5 community, with ESCO's
constraint created first:

```
CREATE CONSTRAINT esco_skill_id IF NOT EXISTS FOR (n:Skill) REQUIRE n.id IS UNIQUE
CREATE CONSTRAINT sfia_probe_skill_id IF NOT EXISTS FOR (n:Skill) REQUIRE n.id IS UNIQUE
SHOW CONSTRAINTS … → esco_skill_id only
```

The second is a **silent no-op**. So a suite indexing the canonical label does
not fail; it inherits another suite's schema objects, and `DROP CONSTRAINT
esco_skill_id` would remove this suite's uniqueness guarantee with no message
anywhere. This confirms the O\*NET NOTES' finding independently, which is worth
having: two measurements of the same behaviour on different days is what turns a
report into a rule.

---

## MERGE identity, and the duplicate that validates clean

`_merge_nodes` merges on `:SfiaNode`, the one label every node of this suite
carries and the one its uniqueness constraint is on — never on the kind label.
MERGE matches the **whole pattern, labels included**, so a node already holding
this id under a different label set is invisible to a kind-label MERGE and gets
duplicated rather than matched.

Reproduced here rather than inherited. With a crosswalk-style placeholder created
*before* the suite is loaded:

```
before load   [['Skill']]
load          → SfiaLoadValidationError: 1 node(s) carry an 'sfia:' id without the :SfiaNode label
after load    [['Skill'], ['SfiaNode', 'SfiaSkill', 'Skill']]
```

The duplicate is real: the constraint cannot see the pair, because constraints
are per label and the placeholder carries neither `:SfiaNode` nor `:SfiaSkill`.
Every count would still add up. `validate_load` fails on any node whose id starts
with `sfia:` and which lacks `:SfiaNode`, names the count, and says to label or
remove them — it does not delete them, because deleting another package's node is
not this loader's call.

**A detail worth recording, because it is what made the first attempt at this
probe prove nothing:** the same placeholder MERGE run *after* a successful load
matches the suite's own node instead of creating anything, since that node
already carries `:Skill`. The hazard exists only in the before-load ordering —
which is exactly the ordering a crosswalk built ahead of its suite produces.

> **A load against an empty graph proves the loader *writes*. It does not prove
> the loader *coexists*. Those are different claims, and the one that ships is
> the second.**

That rule is now written down in three packages (`onet/`, `crosswalks/`, here).
Three copies is worse than one shared home and better than nowhere; someone
should pick the home — `TA-memory` or the workspace `AGENTS.md`, both of which
are another repo.

---

## Tests: verified by mutation, not by passing

Every guard below was removed, the suite re-run, and the guard restored. A guard
whose removal leaves the tests green is not a guard.

```
baseline                                      122 passed
hardcode the searchable label list         ->   1 failed
delete both label-interpolation guards     ->   1 failed
hardcode the traversable rel list          ->   1 failed
remove the impostor detection              ->   1 failed
remove the loader's licence guard          ->   1 failed
revert the tag-boundary fix in extract.py  ->   2 failed
```

**One of these started green, which is the point of doing it.** Removing the
`relation_type`-absent assertion from `validate_load` left all 121 tests passing:
the integration test queries the graph for `relation_type`, and the graph has none
whether or not the loader looks. The assertion was correct and load-bearing on
nothing. A test that *seeds* a `relation_type` on an edge and expects validation
to refuse now exists, and the mutation fails as it should.

The same class of over-claiming was avoided in two other places, both learned
from the O\*NET NOTES rather than rediscovered:

- Config-governance tests monkeypatch `SEARCHABLE_LABELS` and `TRAVERSABLE_RELS`
  and require the query to follow. Asserting that the query names today's labels
  cannot distinguish a derived list from a literal that currently agrees.
- `test_the_suite_exposes_the_contract_method_names` is named for what it
  actually checks. `isinstance` against a `runtime_checkable` Protocol checks
  method *names* and nothing else; the signature comparison beside it is what
  catches a drifted default like `enumerate_paths(max_depth=4→3)`.

### The extraction bug, and why it looked fine

The first version of `extract_skill_page` reported **all seven levels defined for
all 147 skills** — a number that is internally consistent, loads cleanly, and is
wrong. Section boundaries are found by matching an `id=` attribute, which sits
*inside* a tag; slicing there leaves half an opening tag in the fragment, and a
half tag has no closing `>` for the tag-stripper to match, so `<div
class="skill_level "` survived as visible text and the emptiness test read it as
content.

It was caught by looking at the numbers rather than by a test — 147 × 7 = 1,029
edges with a perfectly flat distribution is not what a real framework looks like.
The regression test now asserts an empty section is not read as defined, and the
mutation above confirms it fails when the fix is reverted.

---

## Reproduce

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# A throwaway Neo4j. A load DELETES this suite's nodes, so never point it at a
# graph you care about.
docker run -d --name ta-neo4j-sfia -p 7482:7474 -p 7695:7687 \
    -e NEO4J_AUTH=neo4j/sfia-dev neo4j:5-community

export SFIA_NEO4J_URI=bolt://localhost:7695
export SFIA_NEO4J_PASSWORD=sfia-dev

# Under your own SFIA licence. Writes to data/sfia/ — gitignored, never committed.
python -m ta_taxonomies.suites.sfia.fetch --data-dir data/sfia/raw

python -m ta_taxonomies.suites.sfia.load --mode fixture
python -m ta_taxonomies.suites.sfia.load --mode full --data-dir data/sfia/raw

pytest tests/suites/sfia -q
```

`SFIA_NEO4J_*` overrides `NEO4J_*` for exactly one reason: every suite reads the
same `NEO4J_URI`, and one stale export is enough to point a wiping loader at the
graph someone else was using. The resolved URI is printed *before* anything is
deleted.

Tests that need Neo4j carry `pytest.mark.neo4j` and skip when it is absent; they
reload the fixture with `wipe=True`, so re-run `--mode full` afterwards if you
wanted the complete graph.

---

## Not done

- **Level names and generic-attribute names.** Left out pending a ruling on
  whether they count as structure or as SFIA's wording — see the top of this
  file. One line each to add.
- **The category tree from public sources.** SFIA's category view is behind a
  login, so a snapshot-built graph has none. The loader handles it and the CSV
  path carries it; only the public route cannot reach it.
- **SFIA 10.** In public consultation. Codes are renamed, split and retired
  between versions, so a bump is a re-extraction and a fixture rebuild, not a
  constant change.
- **Crosswalks.** No cross-taxonomy links are created here. SFIA bridges
  **skill-to-skill**, not occupation-to-occupation (`ARCHITECTURE.md`), and it
  has no ISCO or SOC anchor at all — so the official-crosswalk strategy that
  serves ESCO ↔ O\*NET has nothing to work with here. The honest starting point
  is that the **level nodes** are the better anchor: SFIA presents its levels as
  a mapping surface for other frameworks, and a level is a claim two frameworks
  can both make. Recorded as a direction for `crosswalks/`, not attempted here.
- **A performance benchmark.** The Locate shape follows PR #9's conclusion, but
  no A/B was measured; with 154 nodes there is nothing here to measure, and the
  index is for the graph this suite lives in rather than for its own size.
- **`ingestion/` extraction.** `db.py` is a near-copy of ESCO's and O\*NET's. The
  shared version belongs in `ingestion/`, and moving two other suites' copies
  there is a change to their owners' code, so it is left for a PR that can touch
  all three.
- **A `Framework` node.** `ARCHITECTURE.md` lists it as canonical vocabulary and
  SFIA is the obvious candidate, but nothing reads it and a node with no reader
  states an identity the graph does not carry.
