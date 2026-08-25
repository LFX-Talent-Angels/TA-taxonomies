"""Regenerate the committed SFIA fixture from a local snapshot.

Use case: after a SFIA version bump, rebuild ``fixture.json`` from the snapshot
under ``data/sfia/raw`` (see ``fetch.py``) rather than hand-editing it.

    python -m ta_taxonomies.suites.sfia.fixtures.build_fixture

Why the fixture is what it is:

* It carries **only** what ADR-0006 §2 permits this repository to store —
  skill codes, skill names, level numbers, structure, and the page slug that
  points at the definition it deliberately does not copy. The extractor has
  already dropped SFIA's text by the time this runs, and ``assert_factual_only``
  re-checks the result before it is written.
* It is a **slice, not the directory.** The A–Z directory of all 147 skill
  names is arguably the SFIA skills directory itself, and publishing that is
  the redistribution the licence reserves. A closed neighbourhood around one
  skill is a test fixture.
* It is **closed under its own related-skill edges**, so the loader's endpoint
  validation has something real to check rather than a list of dangling links.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ta_taxonomies.suites.sfia.extract import assert_factual_only, read_snapshot

FIXTURE_PATH = Path(__file__).resolve().parent / "fixture.json"

# The seed and the two skills added for the level bands they occupy. Chosen so
# the slice spans the whole responsibility axis: TEST reaches down to level 1,
# SLEN and DLMG reach 7, ISCO exists only at 6–7, and ADMN spans 1–6.
SEED_SLUG = "programming-software-development"
EXTRA_CODES = ("ISCO", "ADMN")


def build(data_dir: Path) -> dict[str, Any]:
    doc = read_snapshot(data_dir)
    by_slug = {row["slug"]: row for row in doc["skills"]}
    if SEED_SLUG not in by_slug:
        raise SystemExit(f"seed skill {SEED_SLUG!r} not in the snapshot")

    seed = by_slug[SEED_SLUG]
    wanted = {SEED_SLUG, *seed["related_slugs"]}
    wanted |= {row["slug"] for row in doc["skills"] if row["code"] in EXTRA_CODES}

    skills: list[dict[str, Any]] = []
    for row in sorted(doc["skills"], key=lambda r: r["code"]):
        if row["slug"] not in wanted:
            continue
        skills.append(
            {
                "code": row["code"],
                "name": row["name"],
                "slug": row["slug"],
                "levels": row["levels"],
                # Only links that stay inside the slice: an edge to a skill that
                # is not loaded is a missing endpoint, and the fixture exists to
                # give validation real endpoints to check.
                "related_slugs": [s for s in row["related_slugs"] if s in wanted],
            }
        )

    return {
        "meta": {
            "suite": "sfia",
            "version": doc["meta"]["version"],
            "mode": "fixture",
            "note": (
                "Structure only: codes, names, level numbers and links. "
                "No SFIA descriptive text — see ADR-0006 §2 and the suite NOTES.md."
            ),
        },
        "skills": skills,
        "codes_by_slug": {row["slug"]: row["code"] for row in skills},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the committed SFIA fixture")
    parser.add_argument("--data-dir", type=Path, default=Path("data/sfia/raw"))
    parser.add_argument("--out", type=Path, default=FIXTURE_PATH)
    args = parser.parse_args(argv)

    document = build(args.data_dir)
    # The fixture is committed to a public repository, so it gets the same
    # licence guard the loader applies — before it is written, not after.
    assert_factual_only(
        [
            {"id": s["code"], "source": "sfia", "source_id": s["code"], "pref_label": s["name"]}
            for s in document["skills"]
        ],
        what="sfia fixture",
    )
    args.out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(document['skills'])} skills)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
