# SFIA suite — design notes

Decisions behind the suite, for whoever extends it. The license and the
graph model are in `README.md`.

## Why structure only

SFIA's free license is for internal/personal use; redistribution needs a
fee-bearing license. Skill codes, names, level numbers and the category tree
are the framework's *structure* and are what every SFIA-aware tool already
exposes; the descriptive text is the licensed payload. The suite stores the
former and points at the latter (`sfia_ref`, `url`). `normalize_document`
reads named keys only, so a document that did carry prose could not leak it
into the graph.

## Data source

`fetch.py` parses the public "skills at a glance" page — one table row per
skill with its category, subcategory, code, name, page link and the levels it
is defined at (cells marked `active`). Re-running it reproduces
`data/sfia/sfia9_structure.json` exactly (verified Oct 2026: 147 skills, 672
levels, 22 subcategories, 6 categories). If SFIA changes the page layout,
`fetch` exits non-zero and lists the rows it could not fill.

## Modelling choices

- **Levels are nodes**, not a property: "PROG level 4" is what a person or a
  job posting actually holds, and crosswalks will link to a level, not to the
  whole skill. `Skill.levels` is a convenience copy; the loader checks it
  against the `HAS_LEVEL` edges.
- **IDs**: `sfia:skill:PROG`, `sfia:level:PROG:4`; `source_id` keeps the
  native forms (`PROG`, `PROG-4`). Categories and subcategories have no SFIA
  code, so their `source_id` is the page slug with a `cat:`/`sub:` prefix.
- **No aliases, no descriptions**: the shared Locate tiers coalesce a missing
  `alt_labels`, so the alias tier simply never fires.
- **Search defaults** leave `SkillLevel` out (every level repeats its skill's
  name and would crowd skills off the page); level codes and `kind="level"`
  reach them.
- **No occupations**: `SuiteSchema.skill_rel_types` is empty and there is no
  group scheme. The shared conformance battery
  (`tests/contract/test_suite_conformance.py`) assumes an occupation label, so
  SFIA's equivalent offline checks live in `tests/suites/sfia/` until that
  battery learns about occupation-free suites.

## Open items

- No embedding job yet (`sfia_label_embedding` is absent); Tier 5 is
  keyword-only.
- Skill-to-skill crosswalks (to ESCO / O*NET skills) belong in `crosswalks/`.
