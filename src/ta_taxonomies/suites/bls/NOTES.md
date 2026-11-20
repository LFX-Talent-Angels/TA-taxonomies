# BLS Knowledge Graph

**Taxonomy:** SOC 2018 + BLS Employment Projections 2025–35 · **Graph DB:** Neo4j (local Docker **or** Aura)
**Suite package:** `src/ta_taxonomies/suites/bls/`

The US occupation spine, with the facts BLS publishes about each occupation:
wage, outlook, typical entry education, work experience, on-the-job training,
and the 17 broad EP skills scored 1–5.

## Source & license

| File | URL | SHA-256 (retrieved 2026-11-04) |
|------|-----|------|
| SOC 2018 structure | https://www.bls.gov/soc/2018/soc_structure_2018.xlsx | `ade08af40923266f3a854842e888ca3e93c15b26a147c20a2b12a61f4c4f4077` |
| SOC 2018 definitions | https://www.bls.gov/soc/2018/soc_2018_definitions.xlsx | `f8b9ca7cccea78e30abfc9b96ab8a9dd2632cc8c8b57ee9b38a9b871e8fe2a89` |
| SOC 2018 direct match titles | https://www.bls.gov/soc/2018/soc_2018_direct_match_title_file.xlsx | `f465e9a3de3146b6fcb7ad6e221156031dc57967c683cc5647f59d2144dbc8a5` |
| EP occupation tables (1.2) | https://www.bls.gov/emp/ind-occ-matrix/occupation.xlsx | `08ee182f992d972593e64559ce467bad75967d2228f11ad5e2f86d4ec0ee6c00` |
| EP skills data (scores) | https://www.bls.gov/emp/skills/public-skills-data.xlsx | `3e95a389425e6280a9d2152f3a2b8191317465785e75a2ffa75b554eda8ceb73` |
| EP skill ↔ O*NET element crosswalk | https://www.bls.gov/emp/skills/ep_skill_onet_element_crosswalk.xlsx | `9e29498cb9d0cb5574331ae5616dd17da6fca96d18e0aef9ee16ead4361ad16a` |

BLS publications are in the US public domain
(https://www.bls.gov/opub/copyright-information.htm). The EP skill scores are
computed by BLS from O*NET 30.0 importance ratings, and O*NET is CC BY 4.0, so
the attribution credits both.

Attribution:

> Occupation data from the U.S. Bureau of Labor Statistics (SOC 2018;
> Employment Projections 2025–35; wages from OEWS May 2025), public domain.
> EP skill scores are derived by BLS from the O*NET 30.0 Database (USDOL/ETA),
> used under CC BY 4.0. O*NET® is a trademark of USDOL/ETA.

The files change once a year: EP each late summer, SOC only at a new revision
(next planned for 2028). A new release means new SHA-256 values here and a re-load.

## Downloading

bls.gov refuses automated requests without a User-Agent that names a contact
(HTTP 403), and also refuses a User-Agent that contains a URL. `fetch.py`
requires `BLS_USER_AGENT`, e.g. `TalentAngels-loader/0.1 (you@example.org)`,
and waits one second between files.

## Design decisions

1. **Occupations are the SOC 2018 detailed occupations** (867). Every one gets a
   node, with its definition and its direct-match titles as `alt_labels`
   ("CEO", "Web Developer" …), so searches find it under common names.
2. **EP facts sit on the occupation as properties.** These are employment 2025 and
   2035, change, openings, median annual wage, entry education, experience,
   training, and the Occupational Outlook Handbook link.
   - EP publishes some groups only as one aggregate (e.g. `31-1120` Home health
     and personal care aides). Those facts are copied onto each detailed
     occupation under it, with `projection_code` and `projection_aggregate: true`, so
     nobody mistakes them for figures about that one occupation.
3. **Skills are the 17 EP skills.** `HAS_SKILL` carries `score` (1–5) and BLS's
   `rating` words. `relation_type` is declared Talent Angels policy:
   - `essential` when score ≥ 3.5 (Very or Extremely important)
   - `optional` when 2.5–3.5 (Important)
   - no edge below 2.5

   Measured on the 2025 data: a median of 5 essential and 8 optional skills per occupation.
4. **Groups:** `SOCGroup` nodes for major, minor and broad groups, linked by
   `BROADER_THAN` (broad → minor → major). Each occupation is `CLASSIFIED_UNDER`
   its broad group. Broad searches report SOC major groups, as O*NET does.
5. **Entry education and training are nodes** (`EducationLevel`,
   `TrainingLevel`) with a `rank`, so "which jobs need only a high-school
   diploma?" is a graph walk. Work experience stays a property.
6. **O*NET link as data, not identity.** `onet_soc_codes` lists the O*NET-SOC
   codes BLS used for the skill scores. Suites never share nodes. This is the
   join the Evaluator will use to bring BLS wage and outlook to O*NET routes.
7. **OEWS is not read separately.** EP table 1.2 already carries the OEWS
   May 2025 median annual wage, so one file fewer.

## What BLS is good for, and what not

- **Good for:** wage, outlook, education and training facts; coarse skill
  profiles; the SOC hierarchy; finding a job by its everyday title.
- **Not for:** fine-grained skills. 17 broad skills make routes between jobs
  coarse (most jobs share "Adaptability"), so Pathfinder on BLS gives few
  meaningful routes. Use ESCO or O*NET for skill detail.

## Search

- **Meaning search** (embedded 2026-11-04, 1,464 nodes; education and training are
  skipped) keeps the shared 0.74 cut-off. Gibberish tops out at 0.65, and paraphrases
  score 0.74–0.94 ("someone who fixes cars" → Automotive Technicians, 0.84).
- **Codes:** `15-1252`, `15-1252.00`, `SOC 15-1252` and group codes (`SOC 15-1250`) are
  found whatever kind is asked.
- **Routes:** the branching cap never cuts an edge that reaches the target. A BLS
  skill links about 800 occupations, so without this every route through a
  shared skill was lost.
