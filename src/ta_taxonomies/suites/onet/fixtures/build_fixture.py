"""Regenerate ``fixture.json`` from a local O*NET text distribution.

Use case: the committed fixture is a small ICT slice of real O*NET rows, so it
must be reproducible from the source rather than hand-edited. Run it after an
O*NET release bump and commit the diff.

    python -m ta_taxonomies.suites.onet.fixtures.build_fixture \
        --data-dir data/onet/raw/db_30_3_text

Why a slice and not a sample: the loader's validation checks that relationship
endpoints exist, so the subset has to be closed under its own edges. Taking
whole occupations and every descriptor they rate keeps it closed; taking every
Nth row would not.

Committing these rows is allowed — O*NET is CC BY 4.0, which grants
redistribution with attribution (see fixtures/README.md). It is kept small
anyway, because a fixture is for tests and the full distribution is one
download away.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ta_taxonomies.suites.onet.config import RATED_FILES, RELEASE
from ta_taxonomies.suites.onet.load import read_tsv

# One coherent ICT cluster: the occupations a Locate/Connect demo actually asks
# about, plus enough overlap between them for path enumeration to have
# something to find.
SLICE_CODES: tuple[str, ...] = (
    "15-1252.00",  # Software Developers
    "15-1251.00",  # Computer Programmers
    "15-1243.00",  # Database Architects
    "15-2051.00",  # Data Scientists
)

# Alias and task pools are truncated per occupation: the fixture needs enough
# of each to exercise the alias tier and the task edges, not O*NET's full 57k
# lay titles.
MAX_ALIASES_PER_OCCUPATION = 12
MAX_TASKS_PER_OCCUPATION = 8


def _by_code(rows: Iterable[dict[str, str]], key: str = "O*NET-SOC Code") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row[key], []).append(row)
    return out


def build(data_dir: Path) -> dict[str, Any]:
    codes = set(SLICE_CODES)
    doc: dict[str, Any] = {
        "meta": {
            "suite": "onet",
            "mode": "fixture",
            "release": RELEASE,
            "slice": sorted(codes),
            "source": "https://www.onetcenter.org/database.html",
            "license": "CC BY 4.0 — https://www.onetcenter.org/license_db.html",
            "generated_by": "ta_taxonomies.suites.onet.fixtures.build_fixture",
        }
    }

    doc["occupations"] = [
        row for row in read_tsv(data_dir / "Occupation Data.txt") if row["O*NET-SOC Code"] in codes
    ]
    if len(doc["occupations"]) != len(codes):
        missing = codes - {row["O*NET-SOC Code"] for row in doc["occupations"]}
        raise SystemExit(f"slice codes not present in this release: {sorted(missing)}")

    for filename in RATED_FILES:
        doc[filename] = [
            row for row in read_tsv(data_dir / filename) if row["O*NET-SOC Code"] in codes
        ]

    # Content Model Reference is a lookup, so keep only the entries the slice
    # actually references — the elements it rates, and every ancestor of those.
    referenced: set[str] = set()
    for filename in RATED_FILES:
        for row in doc[filename]:
            element = row["Element ID"]
            while element:
                referenced.add(element)
                element = element.rpartition(".")[0]
    doc["content_model"] = [
        row
        for row in read_tsv(data_dir / "Content Model Reference.txt")
        if row["Element ID"] in referenced
    ]

    tasks_by_code = _by_code(read_tsv(data_dir / "Task Statements.txt"))
    doc["tasks"] = [
        row
        for code in sorted(codes)
        for row in tasks_by_code.get(code, [])[:MAX_TASKS_PER_OCCUPATION]
    ]

    titles_by_code = _by_code(read_tsv(data_dir / "Sample of Reported Titles.txt"))
    doc["reported_titles"] = [
        row
        for code in sorted(codes)
        for row in titles_by_code.get(code, [])[:MAX_ALIASES_PER_OCCUPATION]
    ]
    # Job Titles is the 57k lay-title pool; a handful per occupation is enough
    # to prove the alias tier resolves against it.
    jobtitles_by_code = _by_code(read_tsv(data_dir / "Job Titles.txt"))
    doc["job_titles"] = [
        row
        for code in sorted(codes)
        for row in jobtitles_by_code.get(code, [])[:MAX_ALIASES_PER_OCCUPATION]
    ]

    # Only edges whose both endpoints are inside the slice; the loader drops
    # the rest anyway, and carrying them would make the fixture misleading.
    doc["related_occupations"] = [
        row
        for row in read_tsv(data_dir / "Related Occupations.txt")
        if row["O*NET-SOC Code"] in codes and row["Related O*NET-SOC Code"] in codes
    ]
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the O*NET test fixture")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "fixture.json",
    )
    args = parser.parse_args(argv)
    doc = build(args.data_dir)
    # Compact separators: the fixture is source rows kept verbatim, so it is
    # read by the loader and diffed by row, never by eye.
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    args.out.write_text(text + "\n", encoding="utf-8")
    sizes = {k: len(v) for k, v in doc.items() if isinstance(v, list)}
    print(json.dumps({"out": str(args.out), "rows": sizes}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
