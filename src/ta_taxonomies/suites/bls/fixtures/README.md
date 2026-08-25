# BLS test fixture

A small slice of **real** BLS rows for contract tests, regenerated from source
rather than hand-edited:

```bash
python -m ta_taxonomies.suites.bls.fixtures.build_fixture --data-dir data/bls/raw
```

## What is in it

Four occupations, chosen so that between them they exercise every branch the
loader has:

| Code | | Why it is here |
|---|---|---|
| `15-1252` | Software developers | shares a broad group with the next one |
| `15-1251` | Computer programmers | gives path enumeration something to find |
| `29-1141` | Registered nurses | its broad group `29-1140` has **no BLS line**, so the fixture contains a reconstructed group — and its minor group is `29-1000`, the case a naive hierarchy rule gets wrong |
| `11-1021` | General and operations managers | a different major group entirely |

Plus every SOC group above them, their ten largest industry cells each, their
aggregate (total / self-employed / wage-and-salary) series, and the four small
code-lookup tables in full.

## Why the whole occupation list is read to build it

The minor group of a SOC code is a **lookup against what BLS publishes**, not
something derivable from the code (see `../NOTES.md`). A fixture built from the
slice alone would resolve `29-1141`'s ancestors against four codes and invent a
minor group that does not exist. `build_fixture` therefore reads the full
occupation tables to get the published minor set, and only then slices.

## Licence

Works of the US federal government are **not subject to copyright**
(17 U.S.C. §105). Committing real source rows is unambiguously permitted here,
which is not true of every suite in this repo — SFIA stores structure only, and
ESCO and O\*NET commit under CC BY. This is the one adopted source where
"pointer, not payload" is not the binding constraint.

It is kept small anyway: a fixture is for tests, and the full download is one
command away.

**Attribution:** U.S. Bureau of Labor Statistics — Employment Projections
program and Occupational Employment and Wage Statistics.
<https://download.bls.gov/pub/time.series/>
