"""Regenerate ``fixture.json`` from downloaded BLS flat files.

Use case: the committed fixture is a small slice of real BLS rows, so it must
be reproducible from the source rather than hand-edited. Run it after a
projections round bump and commit the diff.

    python -m ta_taxonomies.suites.bls.fixtures.build_fixture --data-dir data/bls/raw

Why a slice and not a sample: ``validate_load`` asserts that every SOC node
reaches a major group and that no relationship endpoint is missing, so the
subset has to be closed under its own edges. Taking whole occupations, every
industry cell they appear in, and every SOC group above them keeps it closed.
Taking every Nth row would not.

Committing these rows is unambiguously allowed: works of the US federal
government are not subject to copyright (17 U.S.C. §105). This is the one
adopted source where "pointer, not payload" is not the binding constraint —
see ``fixtures/README.md``. It is kept small anyway, because a fixture is for
tests and ``fetch.py`` is one command away.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ta_taxonomies.suites.bls.config import RELEASE
from ta_taxonomies.suites.bls.ids import normalize_soc_code, soc_level, soc_parent_chain
from ta_taxonomies.suites.bls.load import SOURCE_FILES, read_tsv

# Four occupations that make a coherent demo and, between them, exercise every
# branch the loader has: two that share a broad group, one whose broad group
# BLS publishes no line for (so the fixture contains a *derived* group), and one
# with a large lay-title pool.
SLICE_CODES: tuple[str, ...] = (
    "15-1252",  # Software developers
    "15-1251",  # Computer programmers — same broad group as 15-1252
    "29-1141",  # Registered nurses — the largest occupation with lay titles
    "11-1021",  # General and operations managers
)

# Industry cells are truncated per occupation: the fixture needs enough to
# exercise EMPLOYED_IN, path enumeration and share-based scoring, not all 420.
MAX_INDUSTRIES_PER_OCCUPATION = 10
MAX_LAY_TITLES_PER_OCCUPATION = 12


def build(data_dir: Path) -> dict[str, Any]:
    codes = {normalize_soc_code(code) for code in SLICE_CODES}

    tables: dict[str, list[dict[str, str]]] = {}
    for key, (subdir, filename) in SOURCE_FILES.items():
        path = data_dir / subdir / filename
        if not path.exists():
            raise SystemExit(
                f"missing {path}; run "
                f"`python -m ta_taxonomies.suites.bls.fetch --out {data_dir}` first"
            )
        tables[key] = read_tsv(path)

    # The minor group of a code is a lookup against what BLS publishes, not a
    # derivation (``ids.soc_minor_candidates``), so the *whole* published minor
    # set has to be read before the slice's ancestors can be named. A fixture
    # built from the slice alone would resolve them against 4 codes and put
    # Registered nurses under a minor group that does not exist.
    published = {normalize_soc_code(row["occ_code"]) for row in tables["ep_occupation"]}
    published |= {normalize_soc_code(row["occupation_code"]) for row in tables["oe_occupation"]}
    minor_groups = {code for code in published if soc_level(code) == "minor"}

    # Every ancestor of every sliced code, so the spine in the fixture is
    # rooted exactly as it is in a full load.
    wanted = set(codes)
    for code in codes:
        wanted.update(soc_parent_chain(code, minor_groups))

    doc: dict[str, Any] = {
        "meta": {
            "suite": "bls",
            "mode": "fixture",
            "release": RELEASE,
            "slice": sorted(codes),
            "source": "https://download.bls.gov/pub/time.series/",
            "license": "US federal government work — public domain (17 U.S.C. §105)",
            "generated_by": "ta_taxonomies.suites.bls.fixtures.build_fixture",
        }
    }

    # Small lookup tables go in whole: they are a few lines each and slicing
    # them would only make the fixture differ from the source for no gain.
    for key in ("ep_dates", "ep_eductrn", "ep_otjt", "ep_wkex"):
        doc[key] = tables[key]

    doc["ep_occupation"] = [
        row for row in tables["ep_occupation"] if normalize_soc_code(row["occ_code"]) in wanted
    ]
    doc["oe_occupation"] = [
        row
        for row in tables["oe_occupation"]
        if normalize_soc_code(row["occupation_code"]) in wanted
    ]

    lay_seen: dict[str, int] = {}
    lay_rows: list[dict[str, str]] = []
    for row in tables["ep_laytitle"]:
        code = normalize_soc_code(row["oes_code"])
        if code not in codes:
            continue
        taken = lay_seen.get(code, 0)
        if taken >= MAX_LAY_TITLES_PER_OCCUPATION:
            continue
        lay_seen[code] = taken + 1
        lay_rows.append(row)
    doc["ep_laytitle"] = lay_rows

    # Series for the sliced codes: every aggregate cell (TE1000 carries the
    # occupation's own employment, wage and openings figures, so the fixture is
    # useless without it) plus the largest industry cells by base employment.
    base_by_series = {row["series_id"]: row for row in tables["ep_data"]}

    def base_value(series_id: str) -> float:
        row = base_by_series.get(series_id)
        try:
            return float(row["value"]) if row else 0.0
        except ValueError:
            return 0.0

    by_occupation: dict[str, list[dict[str, str]]] = {}
    aggregates: list[dict[str, str]] = []
    for row in tables["ep_series"]:
        code = normalize_soc_code(row["occ_code"])
        if code not in wanted:
            continue
        if row["ind_code"].startswith("TE1"):
            aggregates.append(row)
        else:
            by_occupation.setdefault(code, []).append(row)

    series_rows = list(aggregates)
    for code in sorted(by_occupation):
        rows = sorted(
            by_occupation[code], key=lambda r: (-base_value(r["series_id"]), r["series_id"])
        )
        series_rows.extend(rows[:MAX_INDUSTRIES_PER_OCCUPATION])
    doc["ep_series"] = series_rows

    kept_series = {row["series_id"] for row in series_rows}
    doc["ep_data"] = [row for row in tables["ep_data"] if row["series_id"] in kept_series]
    doc["ep_aspect"] = [row for row in tables["ep_aspect"] if row["series_id"] in kept_series]

    kept_industries = {row["ind_code"] for row in series_rows}
    doc["ep_industry"] = [
        row for row in tables["ep_industry"] if row["ind_code"] in kept_industries
    ]

    if len(doc["ep_occupation"]) < len(codes):
        missing = codes - {normalize_soc_code(r["occ_code"]) for r in doc["ep_occupation"]}
        raise SystemExit(f"slice codes not present in this round: {sorted(missing)}")
    return doc


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the BLS suite fixture")
    parser.add_argument("--data-dir", type=Path, default=Path("data/bls/raw"))
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parent / "fixture.json"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    doc = build(args.data_dir)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {key: len(value) for key, value in doc.items() if isinstance(value, list)}
    print(json.dumps({"out": str(args.out), "rows": counts}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
