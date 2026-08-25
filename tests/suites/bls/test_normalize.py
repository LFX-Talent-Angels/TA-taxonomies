"""Normalization: source rows → MERGE payloads. No Neo4j.

The tests worth having here are the ones about *what the numbers mean*. BLS
publishes 796,810 values keyed by a two-character ``aspect_type`` and ships no
lookup file for it, so this suite's reading of those codes is the one thing in
the package that could be wrong without anything else noticing.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from ta_taxonomies.suites.bls.config import BASE_YEAR, PROJECTION_YEAR, WAGE_YEAR
from ta_taxonomies.suites.bls.load import (
    BlsLoadValidationError,
    check_release,
    normalize_document,
    verify_aspect_semantics,
)

DATES = [
    {"data_source": "optd", "base_year": str(BASE_YEAR), "proj_year": str(PROJECTION_YEAR)},
    {"data_source": "io_matrix", "base_year": str(BASE_YEAR), "proj_year": str(PROJECTION_YEAR)},
    {"data_source": "wages", "base_year": str(WAGE_YEAR), "proj_year": str(WAGE_YEAR)},
]


def _doc() -> dict[str, Any]:
    """A two-occupation slice with one industry cell, shaped like the real files.

    ``15-1252`` is detailed; ``15-1250`` and ``15-1200`` are published groups;
    ``15-0000`` is deliberately *absent*, so the derived-ancestor pass has
    something to derive.
    """
    return {
        "ep_dates": copy.deepcopy(DATES),
        "ep_eductrn": [{"eductrn_code": "3", "eductrn_desc": "Bachelor's degree"}],
        "ep_otjt": [{"otjt_code": "6", "otjt_text": "None"}],
        "ep_wkex": [{"wkex_code": "4", "wkex_text": "None"}],
        "ep_occupation": [
            {"occ_code": "15-1252", "occ_title": "Software developers", "display_level": "4"},
            {
                "occ_code": "15-1250",
                "occ_title": "Software and web developers",
                "display_level": "3",
            },
            {"occ_code": "15-1200", "occ_title": "Computer occupations", "display_level": "2"},
        ],
        "oe_occupation": [
            {
                "occupation_code": "151252",
                "occupation_name": "Software Developers",
                "occupation_description": "Develop and test computer applications.",
                "display_level": "4",
            }
        ],
        "ep_laytitle": [
            {"oes_code": "15-1252", "lay_title": "Application Developer"},
            {"oes_code": "15-1252", "lay_title": "Application Developer"},
        ],
        "ep_industry": [
            {"ind_code": "5415A1", "ind_title": "Computer systems design", "display_level": "3"},
            {"ind_code": "TE1000", "ind_title": "Total, all industries", "display_level": "0"},
            {"ind_code": "TE1100", "ind_title": "Self-employed workers", "display_level": "1"},
        ],
        "ep_series": [
            {
                "series_id": "EPU151252TE1000",
                "occ_type": "L",
                "ind_type": "S",
                "occ_code": "15-1252",
                "ind_code": "TE1000",
                "wkex_code": "4",
                "otjt_code": "6",
                "eductrn_code": "3",
                "series_title": "Software developers in Total, all industries",
            },
            {
                "series_id": "EPU151252TE1100",
                "occ_type": "L",
                "ind_type": "S",
                "occ_code": "15-1252",
                "ind_code": "TE1100",
                "wkex_code": "4",
                "otjt_code": "6",
                "eductrn_code": "3",
                "series_title": "Software developers, self-employed",
            },
            {
                "series_id": "EPU1512525415A1",
                "occ_type": "L",
                "ind_type": "L",
                "occ_code": "15-1252",
                "ind_code": "5415A1",
                "wkex_code": "4",
                "otjt_code": "6",
                "eductrn_code": "3",
                "series_title": "Software developers in Computer systems design",
            },
        ],
        "ep_data": [
            {"series_id": "EPU151252TE1000", "value": "1693.8"},
            {"series_id": "EPU151252TE1100", "value": "27.1"},
            {"series_id": "EPU1512525415A1", "value": "500.0"},
        ],
        "ep_aspect": [
            {"series_id": "EPU151252TE1000", "aspect_type": "PR", "value": "1961.4"},
            {"series_id": "EPU151252TE1000", "aspect_type": "A1", "value": "267.7"},
            {"series_id": "EPU151252TE1000", "aspect_type": "A2", "value": "15.8"},
            {"series_id": "EPU151252TE1000", "aspect_type": "A3", "value": "1.6"},
            {"series_id": "EPU151252TE1000", "aspect_type": "A4", "value": "115.2"},
            {"series_id": "EPU151252TE1000", "aspect_type": "A5", "value": "133080"},
            {"series_id": "EPU151252TE1100", "aspect_type": "PR", "value": "31.4"},
            {"series_id": "EPU151252TE1100", "aspect_type": "A1", "value": "4.3"},
            {"series_id": "EPU1512525415A1", "aspect_type": "PR", "value": "600.0"},
            {"series_id": "EPU1512525415A1", "aspect_type": "A1", "value": "100.0"},
            {"series_id": "EPU1512525415A1", "aspect_type": "A2", "value": "20.0"},
            {"series_id": "EPU1512525415A1", "aspect_type": "A6", "value": "29.5"},
            {"series_id": "EPU1512525415A1", "aspect_type": "A7", "value": "12.0"},
        ],
    }


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


# --- the spine -------------------------------------------------------------


def test_the_spine_materialises_ancestors_bls_never_published() -> None:
    """15-0000 is in no source row here; without it the tree has no root."""
    payload = normalize_document(_doc())
    groups = _by_id(payload["soc_groups"])

    assert "bls:soc:15-0000" in groups
    derived = groups["bls:soc:15-0000"]
    assert derived["extra"]["title_source"] == "derived"
    assert derived["pref_label"] == ""
    # A derived group is still a real SOC code with a real level, and it still
    # carries its own ancestry — it is only the *title* BLS did not publish.
    assert derived["extra"]["soc_level"] == "major"


def test_every_node_carries_its_complete_ancestry_as_properties() -> None:
    """Roll-up must not depend on which groups happen to have a node.

    This is what makes the suite a spine: a crosswalk landing on any code can
    name its broad, minor and major group without traversing anything.
    """
    payload = normalize_document(_doc())
    occupation = _by_id(payload["occupations"])["bls:occupation:15-1252"]

    assert occupation["extra"]["soc_broad"] == "15-1250"
    assert occupation["extra"]["soc_minor"] == "15-1200"
    assert occupation["extra"]["soc_major"] == "15-0000"


def test_major_groups_are_roots_and_nothing_hangs_off_the_grand_total() -> None:
    payload = normalize_document(_doc())
    parents = {row["from_id"]: row["to_id"] for row in payload["broader_than"]}

    assert parents["bls:occupation:15-1252"] == "bls:soc:15-1250"
    assert parents["bls:soc:15-1250"] == "bls:soc:15-1200"
    assert parents["bls:soc:15-1200"] == "bls:soc:15-0000"
    assert "bls:soc:15-0000" not in parents


# --- what the numbers mean -------------------------------------------------


def test_occupation_figures_come_from_the_total_all_industries_series() -> None:
    payload = normalize_document(_doc())
    extra = _by_id(payload["occupations"])["bls:occupation:15-1252"]["extra"]

    assert extra["employment_base"] == 1693.8
    assert extra["employment_projected"] == 1961.4
    assert extra["employment_change"] == 267.7
    assert extra["employment_change_percent"] == 15.8
    assert extra["openings_annual_average"] == 115.2
    assert extra["median_annual_wage"] == 133080
    assert extra["percent_self_employed"] == 1.6
    assert extra["typical_education"] == "Bachelor's degree"


def test_class_of_worker_splits_do_not_become_industries() -> None:
    """TE1100 is "Self-employed workers", which is not a place anyone works.

    Loading it as an industry would put it beside "Computer systems design" in
    every answer to "which industries employ X" — wrong in kind, and the sort
    of wrongness that reads as data.
    """
    payload = normalize_document(_doc())
    industries = {row["code"] for row in payload["industries"]}
    assert industries == {"5415A1"}

    extra = _by_id(payload["occupations"])["bls:occupation:15-1252"]["extra"]
    assert extra["employment_self_employed_base"] == 27.1
    assert extra["employment_self_employed_projected"] == 31.4


def test_industry_cells_become_edges_carrying_both_matrix_dimensions() -> None:
    payload = normalize_document(_doc())
    (edge,) = payload["employed_in"]

    assert edge["from_id"] == "bls:occupation:15-1252"
    assert edge["to_id"] == "bls:industry:5415A1"
    assert edge["employment_base"] == 500.0
    assert edge["industry_share_of_occupation_base"] == 29.5
    assert edge["occupation_share_of_industry_base"] == 12.0
    # Without these two a caller cannot pick a non-overlapping slice, and
    # summing employment over every edge double-counts.
    assert edge["occupation_type"] == "L"
    assert edge["industry_type"] == "L"


# --- titles ----------------------------------------------------------------


def test_the_projections_title_wins_and_the_oes_form_is_kept_as_an_alias() -> None:
    """Both are real BLS titles; someone who typed either typed a real one."""
    payload = normalize_document(_doc())
    occupation = _by_id(payload["occupations"])["bls:occupation:15-1252"]

    assert occupation["pref_label"] == "Software developers"
    assert "Software Developers" in occupation["alt_labels"]
    assert occupation["description"].startswith("Develop and test")
    # A repeated lay title is collapsed, and the preferred title is not an
    # alias of itself.
    assert occupation["alt_labels"].count("Application Developer") == 1
    assert "Software developers" not in occupation["alt_labels"]


# --- guards ----------------------------------------------------------------


def test_a_different_projection_round_stops_the_load() -> None:
    """Loading 2026–36 under the 2024–34 labels would mislabel every node."""
    doc = _doc()
    doc["ep_dates"][0]["proj_year"] = "2036"
    with pytest.raises(BlsLoadValidationError, match="written for"):
        normalize_document(doc)


def test_aspect_meanings_are_re_derived_and_a_change_stops_the_load() -> None:
    """``ep/`` ships no aspect_type lookup, so the identity is the only check."""
    doc = _doc()
    for row in doc["ep_aspect"]:
        if row["series_id"] == "EPU151252TE1000" and row["aspect_type"] == "A1":
            row["value"] = "999.9"
    with pytest.raises(BlsLoadValidationError, match="no longer mean"):
        normalize_document(doc)


def test_the_aspect_check_actually_checked_something() -> None:
    """A guard that silently checks zero rows passes for the wrong reason."""
    payload = normalize_document(_doc())
    checks = payload["_checks"][0]
    assert checks["a1_identity"] == 3
    assert checks["spine_derived"] == 1  # only 15-0000


def test_a_wage_appearing_on_an_industry_cell_stops_the_load() -> None:
    """Median wage is read as the occupation's own figure.

    If BLS started publishing one per industry cell, this suite would silently
    keep reading the total-all-industries one and be right by luck, or read a
    cell's and be wrong with no warning. Neither is acceptable, so it fails.
    """
    doc = _doc()
    doc["ep_aspect"].append(
        {"series_id": "EPU1512525415A1", "aspect_type": "A5", "value": "140000"}
    )
    with pytest.raises(BlsLoadValidationError, match="occupation-level aspect"):
        normalize_document(doc)


def test_a_share_outside_the_percent_range_stops_the_load() -> None:
    doc = _doc()
    for row in doc["ep_aspect"]:
        if row["aspect_type"] == "A6":
            row["value"] = "129.5"
    with pytest.raises(BlsLoadValidationError, match="percent range"):
        normalize_document(doc)


def test_a_repeated_series_id_stops_the_load_rather_than_resolving_last_wins() -> None:
    """A key collision that resolves silently is indistinguishable from absence."""
    doc = _doc()
    doc["ep_series"].append(dict(doc["ep_series"][0]))
    with pytest.raises(BlsLoadValidationError, match="duplicate series id"):
        normalize_document(doc)


def test_a_conflicting_employment_value_stops_the_load() -> None:
    doc = _doc()
    doc["ep_data"].append({"series_id": "EPU151252TE1000", "value": "1700.0"})
    with pytest.raises(BlsLoadValidationError, match="conflicting employment"):
        normalize_document(doc)


def test_an_identical_repeat_is_harmless() -> None:
    """Only a *conflicting* repeat is a problem; an exact one loses nothing."""
    doc = _doc()
    doc["ep_data"].append({"series_id": "EPU151252TE1000", "value": "1693.8"})
    payload = normalize_document(doc)
    assert (
        _by_id(payload["occupations"])["bls:occupation:15-1252"]["extra"]["employment_base"]
        == 1693.8
    )


def test_release_check_needs_a_projection_round_to_check_against() -> None:
    with pytest.raises(BlsLoadValidationError, match="names no projection round"):
        check_release([{"data_source": "wages", "base_year": "2024", "proj_year": "2024"}])


def test_verify_aspect_semantics_can_be_called_on_its_own() -> None:
    """It is the load's most load-bearing claim, so it is directly testable."""
    checked = verify_aspect_semantics(
        {"s1": 100.0}, {"s1": {"PR": 110.0, "A1": 10.0, "A2": 10.0}}, {"s1"}
    )
    assert checked == {"a1_identity": 1, "a2_identity": 1}
