# JobTech Knowledge Graph

**Taxonomy:** JobTech Taxonomy version 31 (2026-08-12), Arbetsförmedlingen · **Graph DB:** Neo4j
**Suite package:** `src/ta_taxonomies/suites/jobtech/`

Sweden's labour-market taxonomy: occupation names grouped under SSYK 2012,
skills grouped under skill headlines, the skills each SSYK unit group asks for,
how far one occupation can substitute for another, and JobTech's own matches to
ESCO. Labels are **Swedish**.

## Source & license

Bulk JSON from https://data.jobtechdev.se/taxonomy/version/31/query/ (no key).
The direct essential/optional skills of an occupation are not in any bulk file,
so they come from the public GraphQL API
(saved as `data/jobtech/raw/occupation-essential-optional-skills.json`)
(`https://taxonomy.api.jobtechdev.se/v1/taxonomy/graphql`, `version: "31"`).

| File (`query/<name>/<name>.json`) | SHA-256 (retrieved 2026-11-04) |
|------|------|
| `concepts-and-common-relations` | `634fd9d848a172747e54e3e487160a912ae6c0fcb8219c50998767c8c9aacbcc` |
| `substitutability-relations-between-occupations` | `8761f938505ab9f276b8054d235c9a574bf2da1ff56c3761d9ce836f6fc93c60` |

Attribution:

> Occupation and skill data from the JobTech Taxonomy (version 31) by
> Arbetsförmedlingen, the Swedish Public Employment Service, released under
> CC0 1.0.

CC0 needs no attribution, but we credit the source anyway.

## What version 31 holds (measured 2026-11-04)

| Concept | Count |
|---|---|
| occupation-name | 2,105, all under one SSYK level-4 group and one or more ISCO-08 groups |
| ssyk-level-1 / 2 / 3 / 4 | 10 / 46 / 147 / 400 |
| skill | 6,752, each under one skill headline |
| skill-headline | 58 |
| SSYK-4 → skill (`related`) | 17,884 links |
| occupation → skill, direct (`essential` / `optional`, API only) | 3,021 links on 897 occupations; only 144 have an essential skill |
| occupation ↔ occupation substitutability | 4,394 links, each with a percentage (25 / 50 / 75 …) |
| occupation → ESCO occupation match | **2,105 / 2,105** have one (982 exact; also close, broad, narrow) |
| skill → ESCO skill match | 5,968 / 6,752 (88%) |
| ESCO URIs found in our ESCO graph | **16,974 / 16,974** |

## Design decisions

1. **Nodes:**
   - occupation names (`Occupation`)
   - SSYK groups, levels 1–4 (`SSYKGroup`, id by SSYK code)
   - skills (`Skill`)
   - skill headlines (`SkillHeadline`)

   Other concept types (job titles, keywords, regions, SNI, SUN …) are not loaded.
2. **Skills of an occupation:** `HAS_SKILL` with `relation_type`:
   - `essential` / `optional`: JobTech's own direct link (API).
   - `group`: the occupation's SSYK-4 unit group asks for the skill
     (`related`), and JobTech gives no direct link. Every occupation gets
     these, so occupations in one unit group share them.

   `group` counts as **optional** in `suite_schema`, so a skill gap never
   presents a group skill as essential. The unit group also carries its own
   `HAS_SKILL` edges (`relation_type: "group"`).
3. **Substitutability:** `(A)-[:SUBSTITUTES {substitutability_pct}]->(B)`
   when JobTech lists B under A's `substitutes`. Its `substituted_by` list is the
   exact inverse (checked: 4,394 pairs both ways). The percentages are 25 or 75.
   **Reading: "a person in A can do B's job to that degree".** This is inferred
   from the data, because the API docs don't define it. A basic nurse (Sjuksköterska,
   grundutbildad) substitutes a geriatric nurse at 75% and a children's nurse at
   25%, which only fits that reading. Chat answers "what can A move to?" with A's
   outgoing edges.
4. **ESCO matches are data, not identity.** Each occupation and skill stores
   `esco_exact`, `esco_close`, `esco_broad` and `esco_narrow` (lists of ESCO
   URIs). Nothing links to an ESCO node, and suites never import each other.
   These URIs are the bridge an English question can take to a Swedish
   occupation (see the plan, §7).
5. **Codes:**
   - an occupation's `code` is its concept id, so a pasted id finds it
   - an SSYK group's `code` is its SSYK code
   - occupations also carry `ssyk_code` (their unit group) and `isco_codes`
     (from the ISCO concepts' ESCO URIs, `…/isco/C7512` → `7512`)

   Broad searches report **SSYK major groups** (the first digit).
6. **Full-text analyzer:** `standard-no-stop-words`, not `swedish`. Locate's
   contains tier sends wildcard terms, which Lucene does not stem, so a stemmed
   index would miss them.

## Search

- **Meaning search** (embedded 2026-11-04, 9,518 nodes) requires a score of **0.88**,
  not the shared 0.74. The model is English and the labels are Swedish:
  - gibberish reached 0.756
  - English words landed on junk ("baker" → Kantning, 0.75)
  - Swedish near-matches score 0.90+ ("sjukskoterska" → Sjuksköterska)
- **Compound words:** `compound_min_len=5`. "utvecklare" also finds
  *Mjukvaruutvecklare*, after the word-start titles, flagged `compound_part`.
- **English questions** reach JobTech through its own ESCO matches (TA-agents
  `assistant/crosswalk.py`), never through meaning search.

## Known limits

- **Swedish only.** English questions find nothing by words, and the English
  embedding model cannot read Swedish well. Bridges are planned (ESCO matches).
  False friends exist: *chef* means manager.
- Occupations in one SSYK-4 group share their `group` skills, so a skill gap
  between siblings is mostly empty, unless JobTech gives direct skills.
