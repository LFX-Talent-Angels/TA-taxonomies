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

## Adding a new suite (taxonomy)

A suite is done when the **conformance test** is green for it:
`tests/contract/test_suite_conformance.py`. It runs the same checks on every
suite, so a new one behaves exactly like ESCO and O*NET in search, confidence,
groups and narrowing. Follow the checklist; do not copy another suite's search
code.

### 1. Layout

```
src/ta_taxonomies/suites/<name>/
├── config.py   # SOURCE: Final = "<name>", labels, KIND_ALIASES, CONF_*, index names
├── db.py       # neo4j_driver() from NEO4J_* env vars (copy ESCO's)
├── load.py     # run_load(mode="fixture"|"full", wipe=...) + validate
├── embed.py    # optional: label embeddings for the vector tier
├── fixtures/   # a small committed subset (see 4)
└── tools.py    # the suite class: a LocateConfig + graph walks
```

Add the name to `SuiteName` in `contract/models.py` first (code-owner review).

### 2. Search: describe it, don't write it

`search_nodes` is the same five tiers for every suite and lives in
`suites/_locate.py`. A suite only fills in a `LocateConfig`:

```python
FOO_LOCATE = LocateConfig(
    source=SOURCE,  # "<name>", a SuiteName
    node_label="FooNode",  # umbrella label on every node
    occupation_label="Occupation",
    default_labels=("Occupation", "Skill"),
    kind_aliases=KIND_ALIASES,  # "occupation", "skill", ... -> one label
    kind_expansions={},  # optional: "skill" -> several labels
    fulltext_index="foo_node_text",  # over pref_label + alt_labels
    vector_index="foo_label_embedding",  # 384-dim cosine, optional
    groups=GroupScheme(...),  # optional, see 3
)


class FooSuite:
    def _locator(self) -> Locator:
        return Locator(FOO_LOCATE, self._session, lambda q: _embed_query(q))

    def search_nodes(self, text, kind=None):
        return self._locator().search_nodes(text, kind)

    def search_group(self, text, group):
        return self._locator().search_group(text, group)
```

What every suite gets from the engine, and must not reimplement:

| Tier | `method` | Rule | Confidence |
|---|---|---|---|
| 1 | `exact_pref` | preferred label exactly | 0.95 |
| 2 | `exact_alt` | an alias exactly; merged into tier 4, never an early exit | 0.90 |
| 3 | `casefold_pref` | preferred label apart from case | 0.85 unique / 0.80 several |
| 4 | `contains` | the query **starts a word** in a label or alias ("swe" is not inside "Answering"); title matches rank before alias-only ones | 0.70 |
| 5 | `hybrid_rrf` | only when 1–4 found nothing: keyword list + vector list, fused by rank | 0.75 |

- **One confidence scale.** Confidence says how a match was made. Never put a
  Lucene or cosine score in it. The conformance test checks the scale.
- **Acronyms are double-checked.** An acronym that no title contains ("AI
  engineer", "QA tester") is not trusted on aliases alone; the meaning search
  is asked and both are offered (`alias_unconfirmed`).
- **Broad results report their groups** in `meta.groups` (see 3), and
  `search_group(text, code)` narrows to one group.
- **Bad input never reaches the database**: empty text, unknown kinds and
  unknown group codes are answered with a warning.
- **Never invent a hit**: nothing matched is `not_found`, with no candidates.

### 3. Groups ("which area?")

If the taxonomy groups its occupations (ESCO: ISCO unit groups; O*NET: SOC
major groups from the code), describe the grouping:

```python
GroupScheme(
    name="isco-08",  # reported as meta.group_scheme
    code_expr="n.isco_group",  # Cypher: the group code of an occupation
    member_expr="n.isco_group",  # Cypher: what a group prefix must start
    prefix_for=lambda c: c if c.isdigit() else None,  # validate a user's code
    names_for=lookup_group_names,  # code -> {"id", "label"}
)
```

`prefix_for` must return None for anything that is not a group code; the
conformance test sends it hostile strings. A taxonomy without groups sets
`groups=None` and simply offers no areas.

### 4. Fixture

The committed fixture must hold at least: one occupation whose exact title you
name in the conformance case, two or more occupations whose titles share a
word (the broad query), and, if the suite has groups, occupations in at least
two groups. Keep it small; it loads in CI-sized time.

### 5. Register the conformance case

Add one `SuiteCase` to `_cases()` in `tests/contract/test_suite_conformance.py`
(config, class, fixture loader, exact title, broad query, a sample group
code). Then:

```bash
pytest tests/contract -q                         # offline checks, always
docker run -d --rm --name ta-neo4j-test -p 7688:7687 \
    -e NEO4J_AUTH=neo4j/test-password neo4j:5-community
TA_CONFORMANCE_NEO4J_URI=bolt://localhost:7688 \
TA_CONFORMANCE_NEO4J_PASSWORD=test-password pytest tests/contract -q
```

The live layer **wipes** the database it is given, so it only reads
`TA_CONFORMANCE_NEO4J_URI`, never `NEO4J_URI`. Never point it at the graph you
work with.

### 6. The rest of the contract

- `get_neighbors(node_id, rel_types)`: typed nodes and edges, `node_not_found`
  for an unknown id, only the suite's traversable relationships.
- `enumerate_paths(...)`: bounded and cycle-free, with pruning counts.
- `score_paths(paths, policy)`: a named policy, or a declared stub that says so.
- `suite_schema`: which relationships are skills, which values mean optional,
  and the group relationship, so TA-agents can read the suite without
  knowing its name.

### 7. Wire it into TA-agents

In `TA-agents/src/talent_angels/suites/`:

1. `<name>.py`: open the driver and yield a `SuiteRuntime` (copy `onet.py`).
2. `registry.py`: add the factory to `default_suite_registry()`.
3. `assistant/merge.py`: the display name (`"onet": "O*NET"`).
4. `assistant/suite_select.py`: the spellings a user may type for it.
5. Bump the `ta-taxonomies` pin in `pyproject.toml` to your merge commit.
6. Live check: `uv run ta-agent`, then an exact title, a broad word and an
   area letter, and `pytest tests/integration -q` with `NEO4J_*` set.

### Definition of done

- [ ] `ruff check . && ruff format --check . && mypy src && pytest` green
- [ ] conformance test green offline **and** live on a throwaway Neo4j
- [ ] `run_load(mode="fixture")` and its `validate` stage pass
- [ ] TA-agents: the suite is registered and answers in `uv run ta-agent`
