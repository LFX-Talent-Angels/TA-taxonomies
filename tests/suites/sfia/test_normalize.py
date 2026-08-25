"""Normalization: structure-only document → MERGE payloads (no Neo4j needed).

The category tree is exercised with **synthetic** groups. SFIA's real category
view is behind a login, so the public snapshot carries none and the committed
fixture has none either; inventing an assignment to test against would be making
up SFIA structure, which is worse than a synthetic name that is obviously ours.
"""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.sfia.config import LEVELS
from ta_taxonomies.suites.sfia.ids import SfiaIdError
from ta_taxonomies.suites.sfia.load import (
    SfiaLoadValidationError,
    load_fixture_document,
    normalize_document,
)


def _doc(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "skills": [
            {
                "code": "WIDG",
                "name": "Widget wrangling",
                "slug": "widget-wrangling",
                "levels": [3, 4],
                "related_slugs": ["gadget-grooming"],
            },
            {
                "code": "GADG",
                "name": "Gadget grooming",
                "slug": "gadget-grooming",
                "levels": [5],
                "related_slugs": ["widget-wrangling", "not-loaded"],
            },
        ],
        "codes_by_slug": {"widget-wrangling": "WIDG", "gadget-grooming": "GADG"},
    }
    base.update(overrides)
    return base


def test_all_seven_levels_exist_whatever_the_slice_contains() -> None:
    """The responsibility axis is framework structure, not rows of data.

    A fixture whose skills happen to stop at level 5 must still produce a graph
    with levels 6 and 7 in it; deriving the levels from the loaded skills would
    make the axis depend on the slice.
    """
    payload = normalize_document(_doc())
    assert [row["extra"]["level"] for row in payload["levels"]] == list(LEVELS)
    assert {row["pref_label"] for row in payload["levels"]} == {f"Level {n}" for n in LEVELS}


def test_the_level_ladder_only_steps_one_rung_at_a_time() -> None:
    payload = normalize_document(_doc())
    steps = [(row["from_level"], row["to_level"]) for row in payload["may_lead_to"]]
    assert steps == [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7)]


def test_level_names_are_not_stored() -> None:
    """ADR-0006 §2 authorises level *numbers*; the names are SFIA's wording.

    This is the one place in the repository where those names are written down,
    and they are here as a **denylist** rather than as content — the same status
    as the words in ``LICENSED_FIELD_MARKERS``. Without them the guard cannot
    exist; with them anywhere else, the repository would be storing the thing it
    says it does not.
    """
    payload = normalize_document(_doc())
    blob = repr(payload["levels"]).lower()
    for name in ("follow", "assist", "apply", "enable", "ensure", "initiate", "set strategy"):
        assert name not in blob


def test_a_skill_gets_one_has_level_edge_per_published_level() -> None:
    payload = normalize_document(_doc())
    widget = [e for e in payload["has_level"] if e["from_id"] == "sfia:skill:WIDG"]
    assert sorted(e["level"] for e in widget) == [3, 4]
    assert {e["to_id"] for e in widget} == {"sfia:level:3", "sfia:level:4"}


def test_the_level_band_is_summarised_on_the_skill_node() -> None:
    payload = normalize_document(_doc())
    widget = next(row for row in payload["skills"] if row["code"] == "WIDG")
    assert widget["extra"]["levels"] == [3, 4]
    assert widget["extra"]["min_level"] == 3
    assert widget["extra"]["max_level"] == 4
    assert widget["extra"]["level_count"] == 2


def test_a_skill_node_points_at_the_definition_it_does_not_store() -> None:
    """Pointer, not payload: the address of the text is not the text."""
    payload = normalize_document(_doc())
    widget = next(row for row in payload["skills"] if row["code"] == "WIDG")
    assert widget["extra"]["page_slug"] == "widget-wrangling"
    assert widget["extra"]["source_url"].endswith("/skills/widget-wrangling")


def test_a_related_link_outside_the_slice_is_dropped_not_invented() -> None:
    payload = normalize_document(_doc())
    pairs = {(e["from_id"], e["to_id"]) for e in payload["related_to"]}
    assert pairs == {
        ("sfia:skill:WIDG", "sfia:skill:GADG"),
        ("sfia:skill:GADG", "sfia:skill:WIDG"),
    }
    assert not any("not-loaded" in str(edge) for edge in payload["related_to"])


def test_two_names_for_one_code_stop_the_load() -> None:
    """Last-wins would hide the collision behind whichever row was read second."""
    doc = _doc(
        skills=[
            {"code": "WIDG", "name": "Widget wrangling", "slug": "a", "levels": [3]},
            {"code": "WIDG", "name": "Something else", "slug": "b", "levels": [4]},
        ]
    )
    with pytest.raises(SfiaLoadValidationError, match="two different names"):
        normalize_document(doc)


def test_a_repeated_code_with_the_same_name_is_harmless() -> None:
    doc = _doc(
        skills=[
            {"code": "WIDG", "name": "Widget wrangling", "slug": "a", "levels": [3]},
            {"code": "WIDG", "name": "Widget wrangling", "slug": "a", "levels": [3]},
        ]
    )
    payload = normalize_document(doc)
    assert len({row["id"] for row in payload["skills"]}) == 1


def test_the_category_tree_is_built_when_the_source_carries_one() -> None:
    doc = _doc(
        skills=[
            {
                "code": "WIDG",
                "name": "Widget wrangling",
                "slug": "widget-wrangling",
                "levels": [3],
                "category": "SYNTHETIC CATEGORY",
                "subcategory": "SYNTHETIC SUBCATEGORY",
                "related_slugs": [],
            }
        ],
        codes_by_slug={"widget-wrangling": "WIDG"},
    )
    payload = normalize_document(doc)
    assert [row["pref_label"] for row in payload["categories"]] == ["SYNTHETIC CATEGORY"]
    assert [row["pref_label"] for row in payload["subcategories"]] == ["SYNTHETIC SUBCATEGORY"]
    pairs = {(e["from_id"], e["to_id"]) for e in payload["broader_than"]}
    assert pairs == {
        (
            "sfia:subcategory:synthetic-category/synthetic-subcategory",
            "sfia:category:synthetic-category",
        ),
        ("sfia:skill:WIDG", "sfia:subcategory:synthetic-category/synthetic-subcategory"),
    }


def test_one_subcategory_name_under_two_categories_stays_two_nodes() -> None:
    doc = _doc(
        skills=[
            {
                "code": "WIDG",
                "name": "W",
                "slug": "w",
                "levels": [3],
                "category": "CAT A",
                "subcategory": "SHARED",
                "related_slugs": [],
            },
            {
                "code": "GADG",
                "name": "G",
                "slug": "g",
                "levels": [3],
                "category": "CAT B",
                "subcategory": "SHARED",
                "related_slugs": [],
            },
        ],
        codes_by_slug={"w": "WIDG", "g": "GADG"},
    )
    payload = normalize_document(doc)
    assert len(payload["subcategories"]) == 2


def test_a_skill_with_no_category_produces_no_group_edges() -> None:
    payload = normalize_document(_doc())
    assert payload["categories"] == []
    assert payload["broader_than"] == []


def test_a_level_outside_the_framework_is_refused() -> None:
    doc = _doc(skills=[{"code": "WIDG", "name": "W", "slug": "w", "levels": [9]}])
    with pytest.raises(SfiaIdError):
        normalize_document(doc)


def test_the_committed_fixture_normalizes_to_a_closed_slice() -> None:
    payload = normalize_document(load_fixture_document())
    ids = {row["id"] for row in payload["skills"]}
    for edge in payload["related_to"]:
        assert edge["from_id"] in ids
        assert edge["to_id"] in ids
    assert len(payload["levels"]) == len(LEVELS)
    assert all(row["extra"]["level_count"] >= 1 for row in payload["skills"])
