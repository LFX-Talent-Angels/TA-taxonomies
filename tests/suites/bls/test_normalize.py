"""BLS normalize: document → MERGE payloads (no Neo4j)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from ta_taxonomies.suites.bls.load import (
    BlsLoadValidationError,
    load_fixture_document,
    normalize_document,
    rating_for,
    relation_type_for,
)


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    return load_fixture_document()


@pytest.fixture(scope="module")
def payload(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return normalize_document(doc)


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


def test_fixture_counts(payload: dict[str, list[dict[str, Any]]]) -> None:
    assert len(payload["occupations"]) == 15
    assert len(payload["groups"]) == 23
    assert len(payload["skills"]) == 17
    # One broad group per occupation; every broad and minor group has a parent.
    assert len(payload["classified_under"]) == 15
    levels = [g["extra"]["level"] for g in payload["groups"]]
    assert len(payload["broader"]) == levels.count("broad") + levels.count("minor")


def test_occupation_row(payload: dict[str, list[dict[str, Any]]]) -> None:
    dev = _by_id(payload["occupations"])["bls:occupation:15-1252"]
    assert dev["pref_label"] == "Software Developers"
    assert dev["source"] == "bls"
    assert dev["source_id"] == dev["code"] == "15-1252"
    assert dev["kind"] == "Occupation"
    assert "Software Developer" in dev["alt_labels"] or dev["alt_labels"]
    assert dev["description"]
    extra = dev["extra"]
    assert extra["soc_major"] == "15"
    assert extra["onet_soc_codes"] == ["15-1252.00"]
    assert extra["median_wage_annual"] > 0
    assert extra["entry_education"] == "Bachelor's degree"
    assert extra["projection_aggregate"] is False
    assert extra["projection_cycle"] == "2025-2035"
    assert None not in extra.values()


def test_aggregate_projection_is_flagged(payload: dict[str, list[dict[str, Any]]]) -> None:
    aide = _by_id(payload["occupations"])["bls:occupation:31-1121"]["extra"]
    assert aide["projection_code"] == "31-1120"
    assert aide["projection_aggregate"] is True


def test_no_projection_means_no_facts(payload: dict[str, list[dict[str, Any]]]) -> None:
    legislators = _by_id(payload["occupations"])["bls:occupation:11-1031"]["extra"]
    assert "median_wage_annual" not in legislators
    assert "projection_cycle" not in legislators
    assert not [e for e in payload["has_skill"] if e["from_id"] == "bls:occupation:11-1031"]


def test_skill_edges_follow_the_policy(payload: dict[str, list[dict[str, Any]]]) -> None:
    edges = payload["has_skill"]
    assert edges
    for edge in edges:
        props = edge["props"]
        if props["relation_type"] == "essential":
            assert props["score"] >= 3.5
        else:
            assert props["relation_type"] == "optional"
            assert 2.5 <= props["score"] < 3.5
    dev = {e["to_id"]: e["props"] for e in edges if e["from_id"] == "bls:occupation:15-1252"}
    assert dev["bls:skill:computers-and-information-technology"] == {
        "score": 4.3,
        "rating": "Very important",
        "relation_type": "essential",
    }


def test_thresholds_and_ratings() -> None:
    assert relation_type_for(4.5) == relation_type_for(3.5) == "essential"
    assert relation_type_for(3.49) == relation_type_for(2.5) == "optional"
    assert relation_type_for(2.49) is None
    assert rating_for(4.5) == "Extremely important"
    assert rating_for(3.5) == "Very important"
    assert rating_for(2.5) == "Important"
    assert rating_for(1.5) == "Somewhat important"
    assert rating_for(1.0) == "Not important"


def test_education_and_training_are_ranked_nodes(
    payload: dict[str, list[dict[str, Any]]],
) -> None:
    education = payload["education_levels"]
    ranks = [row["extra"]["rank"] for row in education]
    assert ranks == sorted(ranks)
    assert {"bls:education:bachelor-s-degree", "bls:education:master-s-degree"} <= {
        row["id"] for row in education
    }
    # BLS's training "None" is no training: no node, no edge.
    assert all(row["pref_label"] != "None" for row in payload["training_levels"])
    assert all(e["to_id"] != "bls:training:none" for e in payload["requires_training"])
    assert len(payload["requires_education"]) == 14  # every occupation with EP data


def test_groups_link_upwards(payload: dict[str, list[dict[str, Any]]]) -> None:
    broader = {(e["from_id"], e["to_id"]) for e in payload["broader"]}
    assert ("bls:soc-group:15-1250", "bls:soc-group:15-1200") in broader
    assert ("bls:soc-group:15-1200", "bls:soc-group:15-0000") in broader
    assert {"from_id": "bls:occupation:15-1252", "to_id": "bls:soc-group:15-1250"} in payload[
        "classified_under"
    ]


def test_unknown_category_stops_the_load(doc: dict[str, Any]) -> None:
    bad = copy.deepcopy(doc)
    bad["occupations"][1]["projection"]["entry_education"] = "Wizard school"
    with pytest.raises(BlsLoadValidationError, match="Wizard school"):
        normalize_document(bad)


def test_unknown_skill_stops_the_load(doc: dict[str, Any]) -> None:
    bad = copy.deepcopy(doc)
    bad["occupations"][1]["skills"].append({"name": "Telepathy", "score": 5.0})
    with pytest.raises(BlsLoadValidationError, match="Telepathy"):
        normalize_document(bad)


def test_occupation_without_its_group_stops_the_load(doc: dict[str, Any]) -> None:
    bad = copy.deepcopy(doc)
    bad["groups"] = [g for g in bad["groups"] if g["code"] != "15-1250"]
    with pytest.raises(BlsLoadValidationError, match="15-1250"):
        normalize_document(bad)
