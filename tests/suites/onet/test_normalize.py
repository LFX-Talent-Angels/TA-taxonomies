"""Normalization tests: source rows → MERGE payloads (no Neo4j)."""

from __future__ import annotations

from typing import Any

import pytest

from ta_taxonomies.suites.onet.config import (
    ESSENTIAL_IMPORTANCE_MIN,
    RELATION_TYPE_POLICY,
)
from ta_taxonomies.suites.onet.load import OnetLoadValidationError, normalize_document


def _rating(
    code: str,
    element: str,
    scale: str,
    value: str,
    *,
    n: str = "20",
    se: str = "0.1",
    low: str = "3.0",
    high: str = "4.0",
    suppress: str = "N",
    not_relevant: str = "n/a",
) -> dict[str, str]:
    return {
        "O*NET-SOC Code": code,
        "Element ID": element,
        "Element Name": "Programming",
        "Scale ID": scale,
        "Data Value": value,
        "N": n,
        "Standard Error": se,
        "Lower CI Bound": low,
        "Upper CI Bound": high,
        "Recommend Suppress": suppress,
        "Not Relevant": not_relevant,
        "Date": "08/2025",
        "Domain Source": "Analyst",
    }


def _doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "occupations": [
            {
                "O*NET-SOC Code": "15-1252.00",
                "Title": "Software Developers",
                "Description": "Develop applications.",
            }
        ],
        "content_model": [
            {"Element ID": "2.B.3.e", "Element Name": "Programming", "Description": "Writing."},
            {"Element ID": "2.B.3", "Element Name": "Technical Skills", "Description": ""},
        ],
        "Transferable Skills.txt": [
            _rating("15-1252.00", "2.B.3.e", "IM", "4.5"),
            _rating("15-1252.00", "2.B.3.e", "LV", "5.5"),
        ],
    }
    doc.update(overrides)
    return doc


def test_importance_and_level_rows_pair_onto_one_edge() -> None:
    # O*NET publishes IM and LV as separate rows for the same relationship.
    # Two edges here would double every count downstream.
    payload = normalize_document(_doc())

    assert len(payload["has_skill"]) == 1
    edge = payload["has_skill"][0]
    assert edge["importance"] == 4.5
    assert edge["level"] == 5.5
    assert edge["importance_n"] == 20
    assert edge["importance_lower_ci"] == 3.0
    assert edge["from_id"] == "onet:occupation:15-1252.00"
    assert edge["to_id"] == "onet:element:2.B.3.e"


def test_relation_type_is_derived_and_always_names_its_policy() -> None:
    # The essential/optional flag is a projection of Importance, not something
    # O*NET publishes; an edge carrying the value without the policy would read
    # as source data.
    edge = normalize_document(_doc())["has_skill"][0]
    assert edge["relation_type"] == "essential"
    assert edge["relation_type_policy"] == RELATION_TYPE_POLICY.name
    assert edge["relation_type_policy_version"] == RELATION_TYPE_POLICY.version

    below = _doc(
        **{
            "Transferable Skills.txt": [
                _rating("15-1252.00", "2.B.3.e", "IM", str(ESSENTIAL_IMPORTANCE_MIN - 0.5)),
                _rating("15-1252.00", "2.B.3.e", "LV", "1.0"),
            ]
        }
    )
    assert normalize_document(below)["has_skill"][0]["relation_type"] == "optional"


def test_suppress_flag_on_either_scale_marks_the_edge() -> None:
    doc = _doc(
        **{
            "Transferable Skills.txt": [
                _rating("15-1252.00", "2.B.3.e", "IM", "4.5", suppress="N"),
                _rating("15-1252.00", "2.B.3.e", "LV", "5.5", suppress="Y"),
            ]
        }
    )
    assert normalize_document(doc)["has_skill"][0]["recommend_suppress"] is True


def test_soc_group_is_derived_from_the_code_prefix() -> None:
    payload = normalize_document(_doc())

    assert [g["id"] for g in payload["soc_groups"]] == ["onet:soc:15-1252"]
    assert payload["classified_under"] == [
        {"from_id": "onet:occupation:15-1252.00", "to_id": "onet:soc:15-1252"}
    ]
    # A .00 occupation is coextensive with its SOC code, so the SOC group may
    # borrow its title; a split SOC gets none rather than an invented one.
    assert payload["soc_groups"][0]["pref_label"] == "Software Developers"


def test_split_soc_group_gets_no_borrowed_label() -> None:
    doc = _doc(
        occupations=[
            {
                "O*NET-SOC Code": "11-1011.03",
                "Title": "Chief Sustainability Officers",
                "Description": "",
            }
        ],
        **{
            "Transferable Skills.txt": [
                _rating("11-1011.03", "2.B.3.e", "IM", "4.5"),
                _rating("11-1011.03", "2.B.3.e", "LV", "5.5"),
            ]
        },
    )
    payload = normalize_document(doc)
    assert payload["soc_groups"][0]["pref_label"] == ""


def test_element_ancestors_become_groups_with_broader_edges() -> None:
    payload = normalize_document(_doc())

    assert [e["id"] for e in payload["elements"]] == ["onet:element:2.B.3.e"]
    assert {g["id"] for g in payload["element_groups"]} == {
        "onet:element:2.B.3",
        "onet:element:2.B",
        "onet:element:2",
    }
    assert {(e["from_id"], e["to_id"]) for e in payload["broader_than"]} == {
        ("onet:element:2.B.3.e", "onet:element:2.B.3"),
        ("onet:element:2.B.3", "onet:element:2.B"),
        ("onet:element:2.B", "onet:element:2"),
    }


def test_lay_titles_become_aliases_without_repeating_the_title() -> None:
    doc = _doc(
        job_titles=[
            {"O*NET-SOC Code": "15-1252.00", "Job Title": "Java Developer"},
            {"O*NET-SOC Code": "15-1252.00", "Job Title": "Software Developers"},
        ],
        reported_titles=[
            {"O*NET-SOC Code": "15-1252.00", "Reported Job Title": "Java Developer"},
            {"O*NET-SOC Code": "15-1252.00", "Reported Job Title": "Application Developer"},
        ],
    )
    occupation = normalize_document(doc)["occupations"][0]

    assert occupation["alt_labels"] == ["Java Developer", "Application Developer"]


def test_related_edges_outside_the_slice_are_dropped_not_dangled() -> None:
    doc = _doc(
        related_occupations=[
            {
                "O*NET-SOC Code": "15-1252.00",
                "Related O*NET-SOC Code": "15-2051.00",
                "Relatedness Tier": "Primary-Short",
                "Index": "1",
            }
        ]
    )
    assert normalize_document(doc)["related_to"] == []


def test_renumbered_content_model_fails_loudly() -> None:
    # A silently mis-filed element would attach knowledge ratings to an ability
    # and nothing downstream would notice.
    doc = _doc(
        **{
            "Transferable Skills.txt": [
                _rating("15-1252.00", "1.A.1.a.1", "IM", "4.5"),
                _rating("15-1252.00", "1.A.1.a.1", "LV", "5.5"),
            ]
        }
    )
    with pytest.raises(OnetLoadValidationError, match="does not start with"):
        normalize_document(doc)


def test_importance_outside_the_published_scale_fails_loudly() -> None:
    doc = _doc(
        **{
            "Transferable Skills.txt": [
                _rating("15-1252.00", "2.B.3.e", "IM", "9.9"),
                _rating("15-1252.00", "2.B.3.e", "LV", "5.5"),
            ]
        }
    )
    with pytest.raises(OnetLoadValidationError, match="outside the published"):
        normalize_document(doc)
