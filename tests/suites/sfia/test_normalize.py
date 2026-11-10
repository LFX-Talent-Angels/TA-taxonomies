"""Fixture normalize path without Neo4j."""

import pytest

from ta_taxonomies.suites.sfia.load import (
    SfiaLoadValidationError,
    load_fixture_document,
    normalize_document,
)


def _payload() -> dict:
    return normalize_document(load_fixture_document())


def test_fixture_normalize_counts() -> None:
    payload = _payload()

    assert len(payload["categories"]) == 2
    assert len(payload["subcategories"]) == 2
    assert len(payload["skills"]) == 5
    assert len(payload["skill_levels"]) == 4 + 2 + 5 + 5 + 6
    assert len(payload["has_level"]) == 22
    # 5 skills -> subcategory, 2 subcategories -> category
    assert len(payload["broader"]) == 7


def test_fixture_normalize_shapes() -> None:
    payload = _payload()
    prefixes = {
        "categories": "sfia:category:",
        "subcategories": "sfia:subcategory:",
        "skills": "sfia:skill:",
        "skill_levels": "sfia:level:",
    }
    for key, prefix in prefixes.items():
        for row in payload[key]:
            assert row["id"].startswith(prefix)
            assert row["source"] == "sfia"
            assert row["source_id"]
            assert row["pref_label"]
            assert row["sfia_ref"].startswith("SFIA 9 · ")
            # Structure only: no SFIA prose and no aliases ever reach a row.
            assert "description" not in row and "description" not in row["extra"]
            assert "alt_labels" not in row

    prog = next(s for s in payload["skills"] if s["code"] == "PROG")
    assert prog["id"] == "sfia:skill:PROG"
    assert prog["source_id"] == "PROG"
    assert prog["pref_label"] == "Programming/software development"
    assert prog["sfia_ref"] == "SFIA 9 · PROG"
    assert prog["extra"]["levels"] == [2, 3, 4, 5, 6]
    assert prog["extra"]["url"] == (
        "https://sfia-online.org/en/sfia-9/skills/programming-software-development"
    )

    level = next(lv for lv in payload["skill_levels"] if lv["id"] == "sfia:level:PROG:4")
    assert level["source_id"] == "PROG-4"
    assert level["code"] == "PROG-4"
    assert level["pref_label"] == "Programming/software development (level 4)"
    assert level["sfia_ref"] == "SFIA 9 · PROG level 4"
    assert level["extra"]["level"] == 4 and isinstance(level["extra"]["level"], int)

    cat = next(c for c in payload["categories"] if c["pref_label"] == "Strategy and architecture")
    assert cat["source_id"] == "cat:strategy-and-architecture"
    sub = next(s for s in payload["subcategories"] if s["pref_label"] == "Systems development")
    assert sub["source_id"] == "sub:systems-development"


def test_isco_is_an_sfia_skill_code() -> None:
    # SFIA's "ISCO" (Information systems coordination) is not the ISCO
    # occupation classification: the suite-scoped id and source keep them apart.
    isco = next(s for s in _payload()["skills"] if s["code"] == "ISCO")
    assert isco["id"] == "sfia:skill:ISCO"
    assert isco["source"] == "sfia"
    assert isco["pref_label"] == "Information systems coordination"
    assert isco["extra"]["levels"] == [6, 7]


def test_per_skill_invariants() -> None:
    payload = _payload()
    skills = {s["id"]: s for s in payload["skills"]}
    levels = {lv["id"]: lv for lv in payload["skill_levels"]}
    subs = {s["id"] for s in payload["subcategories"]}
    cats = {c["id"] for c in payload["categories"]}

    for edge in payload["has_level"]:
        skill, level = skills[edge["from_id"]], levels[edge["to_id"]]
        assert level["extra"]["level"] in skill["extra"]["levels"]
        assert level["extra"]["skill_code"] == skill["code"]
    for sid, skill in skills.items():
        targets = [e for e in payload["has_level"] if e["from_id"] == sid]
        assert len(targets) == len(skill["extra"]["levels"]) >= 1
        parents = [e["to_id"] for e in payload["broader"] if e["from_id"] == sid]
        assert len(parents) == 1 and parents[0] in subs
    for sub in subs:
        parents = [e["to_id"] for e in payload["broader"] if e["from_id"] == sub]
        assert len(parents) == 1 and parents[0] in cats


def test_skill_with_unknown_subcategory_is_not_silently_dropped() -> None:
    with pytest.raises(SfiaLoadValidationError, match="unknown subcategory"):
        normalize_document(
            {"skills": [{"code": "PROG", "name": "x", "subcategory": "Nope", "levels": [2]}]}
        )


def test_skill_without_levels_is_rejected() -> None:
    with pytest.raises(SfiaLoadValidationError, match="has no levels"):
        normalize_document({"skills": [{"code": "PROG", "name": "x", "levels": []}]})


def test_prose_in_a_document_never_reaches_a_row() -> None:
    doc = load_fixture_document()
    doc["skills"][0]["description"] = "licensed text that must not be stored"
    payload = normalize_document(doc)
    assert "licensed text" not in repr(payload)
