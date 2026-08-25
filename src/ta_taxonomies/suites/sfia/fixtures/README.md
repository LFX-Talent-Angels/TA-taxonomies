# SFIA test fixture

A **14-skill** slice of SFIA 9 — one skill and its published related skills,
plus two more chosen for the level bands they occupy.

| | |
|---|---|
| Seed | `PROG` Programming/software development, and its 11 related skills |
| Added | `ISCO` (defined only at 6–7) and `ADMN` (1–6) so the slice spans the whole axis |
| Carries | skill code, skill name, page slug, the level numbers each skill is defined at, and the related-skill links that stay inside the slice |
| Carries no | descriptions, level definitions, essence-of-the-level text, guidance notes — see below |

## Why it is a slice and not the directory

Unlike the ESCO and O\*NET fixtures, this one is **not** a filter over a
redistributable dataset. SFIA's licence reserves redistribution for fee-bearing
licensees (TA-workspace ADR-0006 §2), and the A–Z listing of all 147 skill names
*is* the SFIA skills directory. What is committed here is what the ADR names as
factual and unprotected — codes, names, level numbers, structure — for a closed
neighbourhood of one skill. That is a test fixture. The full directory would not
be.

It is closed under its own related-skill edges, so the loader's endpoint
validation has real endpoints to check rather than a list of dangling links.

## Regenerate

Needs a local snapshot fetched under your own SFIA licence (see `../README.md`):

```bash
python -m ta_taxonomies.suites.sfia.fetch --data-dir data/sfia/raw
python -m ta_taxonomies.suites.sfia.fixtures.build_fixture
```

`build_fixture.py` runs the licence guard before it writes, so a regenerated
fixture cannot pick up prose that a future page layout happens to expose.

**Attribution:** SFIA® is a registered trademark of the SFIA Foundation. Using
SFIA requires your own licence:
<https://sfia-online.org/en/about-sfia/licensing-sfia>.
