# Contributing to TA-taxonomies

This repo follows the **project-wide contributing guide** in the workspace:
👉 https://github.com/LFX-Talent-Angels/TA-workspace/blob/main/CONTRIBUTING.md

Quick reminders specific to this code repo:

```bash
git switch -c feature/my-change
# ... code + tests ...
ruff check . && ruff format . && pytest      # keep CI green
git commit -s -m "feat: ..."                  # DCO sign-off is required
git push -u origin feature/my-change
gh pr create --fill
```

- Python 3.11+, package `ta_taxonomies` (src-layout).
- New behavior ships with a pytest test.
- Never commit secrets or `.env` files — use `.env.example`.
- At least one mentor approval is required to merge.

## Adding a new suite

A suite is a class that implements the four methods in `contract/protocols.py`:
`search_nodes`, `get_neighbors`, `enumerate_paths`, `score_paths`.

### `search_nodes` implementation requirements

The method must run keyword tiers in this priority order. Each tier exits early
if it produces a unique result; later tiers are only reached when earlier ones
produce nothing (or are ambiguous and you want to merge).

| Tier | Name | How | Confidence |
|---|---|---|---|
| 1 | `exact_pref` | pref_label == query (case-sensitive) | 0.95 |
| 2 | `exact_alt` | query in alt_labels list | 0.90 (merged into Tier 4) |
| 3 | `casefold_pref` | toLower(pref_label) == toLower(query) | 0.85 unique / 0.80 ambiguous |
| 4 | `contains` | pref_label or alt contains query (case-insensitive) | 0.70 |
| 5 | `hybrid_rrf` | BM25 + vector ANN merged via RRF (optional) | 0.75 |

**Important:** Tier 2 (exact alt) must **not** exit early — it must be merged
into the Tier 4 candidate pool. This prevents a node whose alt_label matches
the query from blocking a node whose pref_label contains the query. The
`group_and_sort_locate()` function in TA-agents re-ranks the merged set via
`lexical_rank()` and promotes the true best match.

### `Candidate.method` values

Set the `method` field on each `Candidate` to one of the strings in the table
above (`exact_pref`, `exact_alt`, `casefold_pref`, `contains`, `hybrid_rrf`).
This is an extensibility hook: TA-agents and tests can inspect it to understand
which retrieval tier produced a result.

### `Candidate.confidence` is a declared policy value

**Never** assign a raw similarity or relevance score to `confidence`. The value
must come from the table above. Lucene BM25 scores and cosine similarities are
not comparable across queries, so they would not mean the same thing twice.

### Opting into hybrid search (Tier 5)

1. Add an `embed.py` module with `build_embeddings(driver)` and
   `create_vector_index(driver)` following the ESCO/O*NET pattern.
2. Create the vector index name as `{suite}_label_embedding` (e.g.
   `sfia_label_embedding`), 384 dimensions, cosine similarity.
3. Add `_match_vector()`, `_embed_query()`, `_embedding_available()`, and
   `_reciprocal_rank_fusion()` to your suite tools (copy from ESCO/O*NET).
4. In `search_nodes()`, after Tier 4 returns empty, call Tier 5.
5. Tier 5 is only reached when Tiers 1–4 all return nothing. This means it
   never affects keyword queries — only purely semantic, no-keyword-overlap
   queries like "someone who helps sick people".
6. Gracefully return `not_found` (not an error) when the vector index has
   not been built yet (`vector_index_missing` warning).
