# O*NET 31.0 measured inventory (v1 freeze)

Measured 2026-09-09 from the official text zip. Counts are facts about the
download, not licensed payloads.

## Pin

| Field | Value |
| --- | --- |
| Version | **31.0** (August 2026) |
| URL | https://www.onetcenter.org/dl_files/database/db_31_0_text.zip |
| SHA-256 | `6883548adf5fde64cf6f801b35d15519c9225f2732c3cab0e281c652d16b23a9` |
| Size | 12.5 MiB zip / 46 TSV files + `Read Me.txt` |
| Dictionary | https://www.onetcenter.org/dictionary/31.0/text/ |
| License | CC BY 4.0 — attribution in [`README.md`](./README.md) |
| Local path | `data/onet/raw/` (gitignored) |

Do not load the Sprint 1 **30.3** dump from `TA-lab`. Diff below: **file names
and columns are unchanged**; row counts moved with the quarterly update.

## 30.3 → 31.0 delta

- File names: none added, none removed (46 shared TSVs).
- Columns on shared files: none added, none removed (sampled by exact header
  equality).
- Content: 208 occupations updated in 31.0 (publisher notes). Job Titles
  **54,269** rows (TAXONOMIES.md still quotes 57,543 from an older cut).

## File table (all TSVs)

| File | Rows | Join / identity columns |
| --- | ---: | --- |
| Occupation Data.txt | 1,016 | `O*NET-SOC Code` unique |
| Job Titles.txt | 54,269 | SOC + `Job Title` |
| Sample of Reported Titles.txt | 8,189 | SOC + `Reported Job Title` |
| Task Statements.txt | 18,838 | `Task ID` unique |
| Task Ratings.txt | 165,780 | Task ID + Scale ID |
| Essential Skills.txt | 18,200 | SOC + Element ID + Scale ID |
| Transferable Skills.txt | 45,500 | SOC + Element ID + Scale ID |
| Software Skills.txt | 31,821 | SOC + `Workplace Example` |
| Content Model Reference.txt | 3,006 | `Element ID` unique |
| Related Occupations.txt | 18,460 | SOC → related SOC |
| Knowledge.txt | 60,060 | SOC + Element ID + Scale ID |
| Abilities.txt | 94,640 | SOC + Element ID + Scale ID |
| Work Activities.txt | 74,702 | SOC + Element ID + Scale ID |
| Work Context.txt | 305,389 | SOC + Element ID + Scale ID |
| Work Styles.txt | 37,422 | SOC + Element ID + Scale ID |
| Job Zones.txt | 923 | SOC |
| Education.txt | 11,495 | SOC + Element ID |
| Training and Experience.txt | 26,812 | SOC + Element ID |
| Occupation Level Metadata.txt | 32,280 | SOC + Item |
| Scales Reference.txt | 33 | `Scale ID` |
| Remaining join/reference files | — | element-to-element links, categories, interests |

## Deep measurements (v1-relevant)

### Occupations

- 1,016 occupations. SOC format still `15-1252.00` (string; keep the trailing
  `.00`).
- Title for `15-1252.00` is **Software Developers** (plural), not "software
  developer".
- **910** occupations have Essential/Transferable skill ratings. **106** do
  not (mostly "All Other" / residual groups). The loader must still create
  those occupation nodes.

### Job titles (Locate aliases)

- 54,269 rows, 44,779 distinct titles, **every** occupation has ≥ 1 title.
- Fan-out per occupation: min 1, median 34, mean 53.4, **max 2,750**
  (`51-9199.00` Production Workers, All Other).
- `15-1252.00` has **93** titles. Exact aliases present: `Software Engineer`,
  `Software Developer`, `Application Developer`.
- Columns: `Job Title`, `Short Title` (`n/a` often), `Source(s)`.
- **Implication:** store as `alt_labels` arrays; Locate **must** use the
  full-text index (Albedo path). Do not scan. Do not cap the array in v1
  unless a load-time memory issue shows up — the result cap is still
  `SEARCH_LIMIT=25`.

### Skills (weighted edges)

- Essential: 10 unique Element IDs × 910 occupations × 2 scales (IM, LV) =
  18,200 rows. Every (SOC, Element) pair has **both** IM and LV.
- Transferable: 25 unique Element IDs × 910 × 2 = 45,500 rows. Same IM+LV
  pairing.
- Columns on both files: `Data Value`, `N`, `Standard Error`, `Lower CI Bound`,
  `Upper CI Bound`, `Recommend Suppress`, `Not Relevant`.
- Scales Reference: **IM** Importance 1–5; **LV** Level 0–7.
- `15-1252.00` Programming (`2.B.3.e`) is Transferable, IM=4.00, LV=4.12
  (LV row has `Recommend Suppress=Y` — preserve the flag, do not drop the
  edge).
- **Implication:** one `HAS_SKILL` edge per (occupation, element) with
  `relation_type` essential|transferable and properties `importance`, `level`,
  plus sample-size/CI/suppress when present. Pair IM+LV at normalize time.
  Named `score_paths` policy is justified.

### Tasks

- 18,838 unique `Task ID`s. 4–40 tasks per occupation (median 20).
- `Task Type`: Core 14,071 / Supplemental 4,349 / n/a 418.

### Related occupations

- 18,460 rows. Every covered occupation has **exactly 20** related SOCs.
- Tiers: `Primary-Short` 4,615, `Primary-Long` 4,615, `Supplemental` 9,230.
- 923 source occupations (matches Job Zones coverage, not all 1,016).

### Software (promoted into v1)

- 31,821 rows, 8,753 distinct `Workplace Example` values, 134 software
  categories (`Element Name`), 923 occupations.
- Flags: Hot Technology 11,571; In Demand 2,425.
- `15-1252.00` has **430** examples (Python-class tools live here, **not** in
  Essential/Transferable). Skipping this file would make Connect for software
  developers miss the tools people actually name.
- Identity is the example *name*, not an O*NET code. v1 must pick a stable
  suite id (see open questions).

### Content Model Reference

- 3,006 unique Element IDs (hierarchy + leaves). Use as skill/software
  category descriptions. Skill rating files already carry `Element Name`.

## Product freeze (load all taxonomy knowledge)

This is **not** an MVP cut. `--mode full` loads every O*NET knowledge file
into the shared Neo4j (`ta-neo4j`, same Bolt as ESCO, `:OnetNode` +
`source="onet"`). A committed **fixture** is only for CI / contract tests: a
small slice of the **same** schema, never a smaller model.

| File | Graph role |
| --- | --- |
| Occupation Data.txt | `:Occupation` |
| Job Titles.txt | occupation `alt_labels` |
| Sample of Reported Titles.txt | more occupation `alt_labels` (survey titles) |
| Task Statements.txt | `:Task` + `PERFORMS_TASK` |
| Task Ratings.txt | ratings on `PERFORMS_TASK` / task nodes |
| Task Categories.txt | category labels for task scales |
| Emerging Tasks.txt | proposed tasks (still O*NET knowledge) |
| Essential Skills.txt | `:Skill` + weighted `HAS_SKILL` essential |
| Transferable Skills.txt | `:Skill` + weighted `HAS_SKILL` transferable |
| Knowledge.txt | `:Knowledge` + weighted occupation edges |
| Abilities.txt | `:Ability` + weighted occupation edges |
| Work Activities.txt | `:WorkActivity` + weighted occupation edges |
| Work Context.txt | `:WorkContext` + weighted occupation edges |
| Work Styles.txt | `:WorkStyle` + weighted occupation edges |
| Software Skills.txt | `:Software` + `USES_SOFTWARE` |
| Content Model Reference.txt | element nodes + descriptions |
| Related Occupations.txt | occupation `RELATED_TO` |
| Job Zones.txt / Job Zone Reference.txt | `:JobZone` + occupation link |
| Education.txt / Education Categories.txt | education profile of occupations |
| Training and Experience.txt + Categories | training/experience profile |
| Career interests + specific interest files | `:Interest` nodes and links |
| GWA / IWA / DWA + Tasks to DWAs | work-activity hierarchy |
| \* to Work Activities / Work Context | cross-domain element links |
| Scales Reference.txt / Level Scale Anchors.txt | how `Data Value` is interpreted |

**Not loaded** (survey operations, not taxonomy knowledge — same idea as ESCO
skipping collection tags):

- `Read Me.txt`
- `Survey Booklet Locations.txt`
- `Occupation Level Metadata.txt`

## Ids and rels

| Kind | `id` | `source_id` |
| --- | --- | --- |
| Occupation | `onet:occupation:<O*NET-SOC>` | SOC code |
| Content-model element | `onet:element:<Element ID>` | Element ID (`2.B.3.e`) |
| Task | `onet:task:<Task ID>` | Task ID |
| Software | `onet:software:<slug of Workplace Example>` | example string |
| Job zone | `onet:job-zone:<n>` | zone number as string |
| Scale | `onet:scale:<Scale ID>` | `IM`, `LV`, … |

`kind` on the node says Skill vs Knowledge vs Ability vs WorkActivity, etc.
Essential vs transferable is `HAS_SKILL.relation_type`, not two id schemes.

Umbrella `:OnetNode`. Shared canonical labels (`Occupation`, `Skill`, `Task`)
plus suite-specific labels (`Software`, `Knowledge`, `Ability`, …).
`source="onet"` on every node. Full-text index `FOR (n:OnetNode)` on
`pref_label` + `alt_labels`, analyzer `standard-no-stop-words`.

Software slug: lowercase, non-alphanumerics → `-`. Load fails if two examples
collapse to the same slug. All job titles are stored (no alias cap).

## Next

Identity helpers + Neo4j schema constants, then the loader that ingests the
full freeze (`--mode full`) and a test fixture cut from the same files.
