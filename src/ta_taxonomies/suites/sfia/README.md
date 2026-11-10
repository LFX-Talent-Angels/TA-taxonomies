# SFIA suite

Skills Framework for the Information Age (SFIA 9): 147 digital skills in 6
categories and 22 subcategories, each defined at a contiguous subset of the
seven responsibility levels (672 skill-level pairs). SFIA has **no
occupations** — it bridges skill-to-skill, not occupation-to-occupation.

## Source & license

- **Source:** SFIA 9, © SFIA Foundation — <https://sfia-online.org>
- **What we read:** the public, no-login
  [skills at a glance](https://sfia-online.org/en/sfia-9/sfia-views/full-framework-view/?path=/glance)
  page — category/subcategory names, skill codes and names, and which levels
  each skill is defined at. The official SFIA spreadsheet (behind free
  registration) is **not** required.
- **License:** SFIA's free license covers internal/personal use only.
  Redistributing SFIA material requires a **fee-bearing license**, and this
  repository is public Apache-2.0. So:

> **Structure only. Store the pointer, fetch the payload at runtime.**
> The graph and every committed file hold only unprotected structure: skill
> codes, names, level numbers, category/subcategory names and slugs, and URL
> pointers. **Never** skill descriptions, level descriptions or guidance
> notes — not in the fixture, not in code, not in tests. Every node carries an
> `sfia_ref` citation (e.g. `SFIA 9 · PROG level 4`) and every skill a `url`
> to its sfia-online.org page; a caller that needs the text fetches it there,
> under its own SFIA license.

The full structure file lives under `data/sfia/` (gitignored). Tests use the
small committed fixture under `fixtures/`.

## Load

```bash
# Local Neo4j (default)
docker compose up -d
cp .env.example .env   # NEO4J_PASSWORD=taxonomies-dev

# Fixture (CI / contract tests): 2 categories, 2 subcategories, 5 skills, 22 levels
python -m ta_taxonomies.suites.sfia.load --mode fixture

# Full SFIA 9 structure: fetch it once (public page, stdlib only), then load
python -m ta_taxonomies.suites.sfia.fetch --out data/sfia/sfia9_structure.json
export SFIA_DATA_PATH=data/sfia/sfia9_structure.json   # the default
python -m ta_taxonomies.suites.sfia.load --mode full
```

Both modes read the same JSON shape, so one `normalize_document` serves both.
The full load validates against the document's own counts (147 skills, 672
skill levels, 22 subcategories, 6 categories), and every load asserts: no
dangling `HAS_LEVEL`/`BROADER_THAN`, no blank identities, every skill level is
one of its skill's `levels`, every skill has at least one level, exactly one
subcategory, and every subcategory exactly one category.

## Graph model

| Label | Identity | Properties |
| --- | --- | --- |
| `Skill` | `id` = `sfia:skill:<CODE>`, `source_id` = `PROG` | `pref_label` (name), `code`, `sfia_ref` = `SFIA 9 · PROG`, `url`, `levels` (ints) |
| `SkillLevel` | `id` = `sfia:level:<CODE>:<n>`, `source_id` = `PROG-4` | `pref_label` = `<name> (level 4)`, `code` = `PROG-4`, `level` (int), `skill_code`, `sfia_ref` = `SFIA 9 · PROG level 4` |
| `Subcategory` | `id` = `sfia:subcategory:<slug>`, `source_id` = `sub:<slug>` | `pref_label` (name), `slug`, `sfia_ref` |
| `Category` | `id` = `sfia:category:<slug>`, `source_id` = `cat:<slug>` | `pref_label` (name), `slug`, `sfia_ref` |

All nodes carry `source = "sfia"`, a `kind`, and the internal `SfiaNode`
label, whose unique `id` constraint gives mixed-kind lookups an indexed
anchor (`Skill` is a canonical label shared with ESCO and O*NET). Codes stay
strings. SFIA's skill code `ISCO` (Information systems coordination) is
unrelated to the ISCO occupation classification — `source` + `source_id` keep
them apart.

| Rel | Meaning |
| --- | --- |
| `HAS_LEVEL` | Skill → SkillLevel, one per level the skill is defined at |
| `BROADER_THAN` | narrower → broader: Skill → Subcategory → Category |

No crosswalk edges live here; cross-taxonomy links belong in `crosswalks/`.

## Tools

`SfiaSuite` implements the contract: `search_nodes`, `search_group`,
`get_neighbors`, `enumerate_paths`, `score_paths` (not implemented — SFIA
publishes no edge weights).

Search runs the shared Locate tiers (`suites/_locate.py`) with the same
confidence scale as every suite. A skill code (`PROG`, case-sensitive)
or level code (`PROG-4`) is looked up first (`exact_code`, 0.95); then exact,
case-insensitive and word-start label matches. SFIA has no aliases, so the
alias tier never fires. Skill levels are left out of the default search (each
shares its skill's name); ask with `kind="level"`, by level code, or walk
`HAS_LEVEL` with `get_neighbors`. `search_group` always answers
`unknown_group`: SFIA has no occupation groups. There is no embedding job yet,
so the last-resort tier uses keywords only.

Path enumeration uses the same bounded, cycle-free BFS as ESCO (depth ≤ 6,
≤ 25 expansions per node, 500-path frontier, ≤ 100 paths); on SFIA's small
tree these caps are never the binding limit. Ties use relationship type and
node ID.
