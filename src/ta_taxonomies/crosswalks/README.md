# crosswalks

Cross-taxonomy links. Explicit, stored, cited — never inferred at query time.

Two halves, because the evidence splits cleanly in two: occupations have a
published correspondence we can transcribe, and skills do not.

---

## What was verified, and how

Every claim below was checked against primary sources and against the loaded
graph on 2026-08-24. Where a number appears, it was measured, not estimated.

### Occupations: a published correspondence exists — and it is a *direct* one

The route this repo was expected to take was
`ESCO → ISCO → SOC → O*NET`. Each hop is real:

- **ESCO → ISCO** is in the graph already. All 3,039 loaded ESCO occupations
  carry exactly one `CLASSIFIED_UNDER` edge, landing on 426 of the 619 loaded
  ISCO groups. No occupation is unclassified and none is doubly classified.
- **ISCO ↔ SOC** is published by BLS on behalf of the SOC Policy Committee.
- **O*NET-SOC → SOC** is published by the O*NET Center and is deterministic:
  the SOC code is the substring before the dot.

But that chain is not the best route, and building on it would have been a
mistake:

1. **It has a version seam.** The BLS ISCO crosswalk is against **SOC 2010**,
   while O*NET-SOC 2019 derives from **SOC 2018**. Using it means a fourth hop
   (SOC 2010 → SOC 2018) and compounding loss at every join.
2. **A direct correspondence already exists.** The European Commission
   (DG EMPL) and the US Department of Labor jointly publish an
   **ESCO ↔ O*NET-SOC 2019** crosswalk:
   <https://www.onetcenter.org/crosswalks.html#esco>

Measured on the published file:

| | |
|---|---|
| Rows | 8,627 |
| Distinct ESCO-side codes | 3,349 (2,997 occupation codes + 352 ISCO group codes) |
| Distinct O*NET-SOC codes | 958 of the 1,016 in the 2019 taxonomy |
| Mean O*NET per ESCO code | 2.58 |
| Exactly one-to-one | 40.9% |

Resolved against the fully loaded ESCO graph:

| | |
|---|---|
| Correspondences that resolve | **8,487** (7,566 occupation-level + 921 group-level) |
| ESCO occupations that cross | **2,959 of 3,039 (97.4%)** |
| ESCO occupations with no row | 80 — recorded as explicit `NoLink` |
| …of those, rescued via their ISCO group | 61 |
| …reachable by neither route | **19** |
| Table codes absent from this ESCO release | 38 (a version seam, reported not swallowed) |

**The correction that matters.** This crosswalk is *published*, but it is not
*deterministic*. Its own technical report describes a fine-tuned BERT model
proposing candidates that human validators then accepted or rejected — the
best model placed the correct concept first for 85% of exact matches. So
"transcription of a published mapping" is right, and "transcription of a
deterministic derivation" is not. `MappingMethod` keeps those apart, and the
loaded value is `model_assisted_validated`.

Two further caveats, both load-bearing:

- The distributed file **has no match-strength column**, even though the
  published methodology defines exact / narrower / broader / close / related.
  Rows load as `MatchStrength.UNSPECIFIED`. Inferring a strength would turn a
  transcription into an assertion.
- ESCO documents a **second version** that adds `related` matches and states it
  went through neither quality assurance nor US DOL validation. It is
  deliberately not registered in `sources.py`.

### Skills: there is no published correspondence, and the granularities do not meet

Checked on both publishers' own indexes of what they publish:

- ESCO's *Other crosswalks* page lists exactly two: **O*NET (occupations)** and
  **NACE (economic activities)**. Neither maps skills.
- O*NET's *Crosswalk Files* page lists seven: MOC, CIP, DOT, RAPIDS, OOH, SOC,
  ESCO. Every one is occupation-level.

So the second premise holds. And the granularity gap is not a detail:

| | Count | Source |
|---|---|---|
| ESCO skills | **13,939** | measured in the loaded graph |
| ESCO skill→occupation edges | **126,051** | measured; two predicates, no weights |
| O*NET Skills elements | **35** | 10 Essential + 25 Transferable, O*NET Content Model |
| O*NET Abilities | 52 | Content Model |
| O*NET Knowledge | 33 | Content Model |

That is roughly **400:1** against the Skills domain alone, and about **116:1**
against Skills + Abilities + Knowledge combined. These are not two views of the
same thing at different resolutions. ESCO enumerates specific competences
("operate a hydraulic press"); O*NET rates a fixed psychometric instrument
("Active Listening", 0–7, with sample size and confidence bounds). A mapping
between them is a research problem, which is exactly what ADR-0006 decision 4
says and why it defers semantic fusion rather than rejecting it.

**Nothing in this package generates skill correspondences.** If it did, they
would be model output wearing the same clothes as the European Commission's
table, and no reader downstream could tell them apart.

---

## The decision the team owns

Occupations are settled: transcribe the published table, cite it, ship it.
Skills are not, and the choice is not a technical one. Three options, with what
each actually costs.

### Option 1 — Show the two side by side, each with its provenance

Never assert a skill correspondence. A query about skills returns ESCO's answer
labelled ESCO and O*NET's answer labelled O*NET, and says plainly that no
published mapping joins them.

- **Costs nothing** to build. Already true today.
- **Cannot be wrong.** There is no claim to be wrong about.
- **Pushes the work to the user.** "Here are 40 ESCO skills and 6 O*NET
  elements, good luck" is not an answer to "what should I learn next".
- **Leaves O*NET's weights stranded.** The 62,580 importance/level ratings —
  the single richest signal in the whole data set, per ADR-0006 — cannot reach
  an ESCO-shaped question.

### Option 2 — One primary skills taxonomy, the other as enrichment

Pick ESCO as the skill spine (13,939 concepts, multilingual, granular). Reach
O*NET through the **occupation** crosswalk that already exists, and attach its
importance/level ratings as context on the occupation, never as a claim about a
specific ESCO skill.

- **Buys the weights without inventing anything.** The join is occupation-level,
  where a published correspondence exists.
- **Honest about what it is.** "Software Developers rate Programming 4.5/7 in
  importance" is a true statement about an O*NET occupation that a user reached
  through a cited crosswalk. It is not a statement about an ESCO skill.
- **Fuzzy at the edges.** An ESCO occupation maps to 2.58 O*NET occupations on
  average; which occupation's ratings do you show, and how do you combine them?
  That aggregation is a modelling decision and must be declared as one under a
  named, versioned policy — the same discipline `score_paths` already requires.
- **Silently privileges Europe.** ESCO as the spine makes the European view the
  default. Defensible, but it should be a decision, not a side effect.

### Option 3 — The project asserts its own correspondences, with a lifecycle

Build a real skill-to-skill mapping that this project owns and stands behind.
Every link carries an owner, a written rationale, and a status:

```
observed → proposed → reviewed → accepted → deprecated
```

`AssertedCorrespondence` in `models.py` is this type. It is designed and tested
and **nothing populates it**. That is deliberate: the type should exist before
the temptation does.

- **This is where the project stops being a reader of other people's taxonomies
  and starts having one.** It is the only option that produces an asset nobody
  else has, and it fits LFDT's Proof of Learning framing — ADR-0006 decision 9
  already declines to source credentials externally on exactly this reasoning.
- **It is ongoing human work, not a one-off load.** 13,939 × 35 is half a
  million candidate pairs. Even a heavily pruned shortlist is a review queue
  measured in months of volunteer attention, and a review queue with no
  reviewers silently becomes a queue of `proposed` claims that nobody should
  trust and somebody eventually will.
- **It needs governance before it needs code.** Who may accept a claim? What
  happens when the owner leaves the project? ESCO ships new releases — what
  reviews the accepted claims against the new version? The lifecycle models
  these questions; it does not answer them.
- **Its worst failure mode is being right often enough.** A mapping that is
  usually correct is more dangerous than one that is obviously broken, because
  nobody checks it. This is why `ASSERTED_CORRESPONDS_TO` is a *different
  relationship type* from `CORRESPONDS_TO` rather than a flag: a query written
  for published data cannot reach project claims by forgetting a filter, and
  only `accepted` claims surface even when a caller opts in.

### These are not exclusive

Option 2 is a reasonable default that Option 3 can grow inside later; Option 1
is the honest fallback wherever neither reaches. The real question is not
"which one" but **whether this project intends to own a skills taxonomy**.
Options 1 and 2 say no and stay cheap. Option 3 says yes, and the cost is
recurring human review, not engineering.

**What the team must decide before anyone writes more code:**

1. Does the project want to own asserted skill correspondences at all?
2. If yes: who owns them, who may review them, and what is the standing
   capacity for review? Without an answer, Option 3 becomes Option 1 with extra
   steps and a misleading table.
3. If no: is Option 2's occupation-mediated enrichment acceptable, and under
   what named policy are ratings aggregated across the 2.58 O*NET occupations
   an ESCO occupation maps to?

---

## Usage

```bash
# CI: committed, license-clean fixture
python -m ta_taxonomies.crosswalks.load --mode fixture

# Coverage report against a populated graph. Strictly read-only, schema
# included — safe to point at someone else's database.
python -m ta_taxonomies.crosswalks.load --mode full --dry-run

# Real load. Requires both the ESCO and O*NET suites to be loaded first.
python -m ta_taxonomies.crosswalks.load --mode full --source esco_onet_2019
```

`--mode` has no default. The pinned snapshot is fetched to
`data/crosswalks/<source_key>.xlsx` (gitignored) and never committed —
pointer, not payload.

```python
from ta_taxonomies.crosswalks import Crosswalks

result = crosswalks.counterparts("esco:occupation:f2b1…", to_suite="onet")
# result.nodes    -> the O*NET occupations
# result.evidence -> ["esco_onet_2019:https://www.onetcenter.org/…"]
# result.warnings -> ["esco_onet_2019: published mapping is model-assisted",
#                     "esco_onet_2019: source publishes no match strength"]
```

An occupation with a recorded absence returns no nodes and a `no_link[…]`
warning — which is a different answer from `not_found`, and deliberately so.

## Invariants

- The loader **never deletes anything a suite owns**. Reset is scoped by
  relationship type, by `source_key`, and by crosswalk-owned labels. Enforced
  by a test that parses this module's own Cypher.
- The loader **never creates endpoints**. A correspondence to a node that does
  not exist fails the load; it is not silently dropped, because a dropped row
  is indistinguishable from a genuine absence.
- Published and asserted correspondences are **different relationship types**,
  not one type with a flag.
- Codes are strings everywhere. ESCO `0110.10` (lieutenant) is in the published
  table; `0110.1` (air force officer) is not. A numeric round-trip merges them
  and hands an air force officer three American police occupations. The fixture
  contains this exact pair and a test pins it.

## Sources

- [O*NET Crosswalk Files](https://www.onetcenter.org/crosswalks.html)
- [The crosswalk between ESCO and O*NET](https://esco.ec.europa.eu/en/about-esco/data-science-and-esco/crosswalk-between-esco-and-onet)
- [O*NET–ESCO Technical Report](https://esco.ec.europa.eu/en/publication/crosswalk-between-esco-and-onet-technical-report)
- [ESCO: Other crosswalks](https://esco.ec.europa.eu/en/use-esco/other-crosswalks)
- [O*NET-SOC 2019 to 2018 SOC](https://www.onetcenter.org/taxonomy/2019/soc.html)
- [O*NET Content Model](https://www.onetcenter.org/content.html)
- [BLS SOC crosswalks](https://www.bls.gov/soc/2018/crosswalks.htm) (ISCO-08
  crosswalk is against SOC 2010; the site blocks automated fetch, so this was
  read via its index and the O*NET/BLS references to it)
