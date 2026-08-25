# BLS/SOC suite

The **occupation spine and employment projections** (ADR-0006, decision 3).
2018 SOC + BLS Employment Projections 2024–34, wages 2024.

**This suite has no skills layer.** That is a property of the source, not a gap
in the package — see `NOTES.md`. Ask O\*NET or ESCO what skills an occupation
needs, then come back here through a crosswalk on the SOC code.

## Source and terms

| Item | Detail |
|---|---|
| **Programs** | Employment Projections (`ep`) · Occupational Employment and Wage Statistics (`oe`) |
| **Download** | `https://download.bls.gov/pub/time.series/` — 12 files, ~71.5 MB |
| **Registration** | **None.** No account, no API key, no click-through. |
| **Licence** | Work of the US federal government — **public domain**, 17 U.S.C. §105 |
| **Attribution** | U.S. Bureau of Labor Statistics, Employment Projections program |

`download.bls.gov` serves these files to a client that identifies itself, which
is what `fetch.py` does — it requires `BLS_CONTACT` and refuses to run without
it. The `.xlsx` workbooks on `www.bls.gov` are behind Akamai bot management and
return 403 to any honest automated client; this suite does not work around that,
and `NOTES.md` records exactly what it costs.

Because BLS is public domain, the committed fixture holds **real source rows**
rather than a hand-made stand-in. This is the one adopted source where
"pointer, not payload" is not the binding constraint.

## Graph model

```text
(:BlsOccupation)-[:BROADER_THAN {levels_skipped}]->(:BlsSocGroup)
(:BlsSocGroup)  -[:BROADER_THAN]->(:BlsSocGroup)
(:BlsOccupation|:BlsSocGroup)-[:EMPLOYED_IN {employment_base, employment_projected,
     employment_change_percent, industry_share_of_occupation_base,
     occupation_share_of_industry_base, occupation_type, industry_type}]->(:BlsIndustry)
```

Occupations and groups carry `:BlsNode` + a suite-scoped label + the canonical
`ARCHITECTURE.md` one. **Industries carry no canonical label**: the shared
vocabulary has no node kind for the economic dimension, which is a contract gap
recorded in `NOTES.md` rather than papered over.

Every node carries its own ancestry as properties (`soc_broad`, `soc_minor`,
`soc_major`), so roll-up is exact even where BLS publishes no line for the
intermediate group.

## Counts (full load)

| | |
|---|---|
| Detailed occupations | 825 |
| SOC groups (457 broad · 95 minor · 23 major) | 575 |
| Industries | 420 |
| `BROADER_THAN` | 1,377 |
| `EMPLOYED_IN` | 110,353 |
| Lay titles · SOC definitions | 6,828 · 830 |

## Tools

The four contract methods, plus one this suite exists for:

```python
suite.search_nodes("CCU Nurse")  # → bls:occupation:29-1141
suite.search_nodes("onet:occupation:15-1252.00")  # → bls:occupation:15-1252
suite.resolve_soc("onet:soc:29-1141")  # → node + full roll-up
suite.resolve_soc("esco:occupation:f2b15a0e-…")  # → warnings: ['no_soc_code']
suite.get_neighbors(node_id, rel_types=["EMPLOYED_IN"])
suite.enumerate_paths(a, b, max_depth=2)
suite.score_paths(paths, PolicyRef(name="bls-projected-growth", version="1"))
```

`resolve_soc` joins on the **SOC code string** and says so in `meta["join"]`. It
is not a stored crosswalk and creates no cross-suite edges — those belong in
`crosswalks/`.

## Run it

```bash
export BLS_NEO4J_URI=bolt://localhost:7694
export BLS_NEO4J_PASSWORD=…
export BLS_CONTACT="you@example.org"

python -m ta_taxonomies.suites.bls.fetch --out data/bls/raw
python -m ta_taxonomies.suites.bls.load  --mode fixture
python -m ta_taxonomies.suites.bls.load  --mode full --data-dir data/bls/raw

pytest tests/suites/bls -q
```

`--mode` is required on purpose: a load starts by deleting this suite's nodes,
so the target must be a choice rather than a default. `BLS_NEO4J_*` overrides
`NEO4J_*` so a wiping loader can be pinned to a throwaway instance, and the
resolved URI is printed before anything is deleted.

Full build notes, measured findings and what was deliberately left out:
[`NOTES.md`](NOTES.md).
