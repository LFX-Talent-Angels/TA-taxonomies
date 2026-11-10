"""JobTech normalize: document → MERGE payloads (no Neo4j)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from ta_taxonomies.suites.jobtech.load import (
    JobtechLoadValidationError,
    load_fixture_document,
    normalize_document,
)


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    return load_fixture_document()


@pytest.fixture(scope="module")
def payload(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return normalize_document(doc)


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


def test_counts(payload: dict[str, list[dict[str, Any]]]) -> None:
    assert len(payload["occupations"]) == 11
    assert len(payload["classified_under"]) == 11
    assert len(payload["skill_headlines"]) == len(payload["skills"])
    levels = [row["extra"]["level"] for row in payload["ssyk_groups"]]
    assert len(payload["group_parents"]) == len(levels) - levels.count(1)


def test_occupation_row(payload: dict[str, list[dict[str, Any]]]) -> None:
    baker = _by_id(payload["occupations"])["jobtech:occupation:XugJ_6hw_wVV"]
    assert baker["pref_label"] == "Bagare/Konditor"
    assert baker["source"] == "jobtech"
    assert baker["code"] == baker["source_id"] == "XugJ_6hw_wVV"
    assert baker["alt_labels"] == ["Bagare", "Konditor"]
    extra = baker["extra"]
    assert extra["ssyk_code"] == "7612"
    assert extra["ssyk_major"] == "7"
    assert extra["isco_codes"] == ["7512"]
    assert (
        "http://data.europa.eu/esco/occupation/1aadb308-432a-4d01-b54b-b4f7f76dd419"
        in (extra["esco_exact"])
    )


def test_ssyk_groups_are_keyed_by_code(payload: dict[str, list[dict[str, Any]]]) -> None:
    groups = _by_id(payload["ssyk_groups"])
    unit = groups["jobtech:ssyk:7612"]
    assert unit["code"] == "7612"
    assert unit["extra"]["level"] == 4
    assert {"from_id": "jobtech:ssyk:7612", "to_id": "jobtech:ssyk:761"} in payload["group_parents"]


def test_skill_relations(payload: dict[str, list[dict[str, Any]]]) -> None:
    edges = payload["occupation_skills"]
    kinds = {e["props"]["relation_type"] for e in edges}
    assert kinds == {"essential", "optional", "group"}
    midwife = [e for e in edges if e["from_id"] == "jobtech:occupation:DEDi_wap_ntF"]
    assert sum(e["props"]["relation_type"] == "essential" for e in midwife) == 2
    # One edge per occupation and skill: a direct link wins over the group's.
    pairs = [(e["from_id"], e["to_id"]) for e in edges]
    assert len(pairs) == len(set(pairs))
    assert all(e["props"]["relation_type"] == "group" for e in payload["group_skills"])


def test_substitutions(payload: dict[str, list[dict[str, Any]]]) -> None:
    assert {
        "from_id": "jobtech:occupation:Sbzf_iBA_MAS",
        "to_id": "jobtech:occupation:17FS_XzU_AQ4",
        "props": {"substitutability_pct": 75},
    } in payload["substitutes"]


def test_another_version_is_refused(doc: dict[str, Any]) -> None:
    bad = copy.deepcopy(doc)
    bad["meta"]["taxonomy_version"] = 30
    with pytest.raises(JobtechLoadValidationError, match="version 30"):
        normalize_document(bad)


def test_occupation_without_its_unit_group_is_refused(doc: dict[str, Any]) -> None:
    bad = copy.deepcopy(doc)
    bad["ssyk_groups"] = [g for g in bad["ssyk_groups"] if g["code"] != "7612"]
    with pytest.raises(JobtechLoadValidationError, match="7612"):
        normalize_document(bad)


def test_skill_without_its_headline_is_refused(doc: dict[str, Any]) -> None:
    bad = copy.deepcopy(doc)
    bad["headlines"] = bad["headlines"][1:]
    with pytest.raises(JobtechLoadValidationError, match="headline"):
        normalize_document(bad)
