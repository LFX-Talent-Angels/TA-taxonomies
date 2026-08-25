"""SFIA identity helpers: codes, levels, slugs, and the ISCO collision."""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.sfia.ids import (
    SfiaIdError,
    category_id,
    level_id,
    normalize_level,
    normalize_skill_code,
    skill_id,
    skill_slug_to_code,
    slugify,
    subcategory_id,
)


def test_a_code_is_upper_cased_because_users_type_lowercase() -> None:
    assert normalize_skill_code("prog") == "PROG"
    assert normalize_skill_code("  PROG  ") == "PROG"


@pytest.mark.parametrize("bad", ["", "PRO", "PROGR", "PR0G", "PROG-1", None])
def test_anything_not_four_letters_is_refused_rather_than_repaired(bad: object) -> None:
    with pytest.raises(SfiaIdError):
        normalize_skill_code(bad)


def test_the_isco_collision_is_resolved_by_the_id_and_not_by_the_code() -> None:
    """SFIA's ISCO is a skill; ESCO's ISCO is an occupation classification.

    The point of ADR-0006 §5: the bare code is the same four characters in both
    places, so it can never be the identity. Only the suite-scoped id is.
    """
    assert normalize_skill_code("ISCO") == "ISCO"
    assert skill_id("ISCO") == "sfia:skill:ISCO"
    assert skill_id("ISCO") != "esco:isco:ISCO"
    assert skill_id("ISCO").startswith("sfia:")


def test_levels_are_ints_inside_the_frameworks_own_range() -> None:
    assert normalize_level("4") == 4
    assert normalize_level(7) == 7
    assert level_id(1) == "sfia:level:1"


@pytest.mark.parametrize("bad", [0, 8, -1, "four", True, None])
def test_a_level_outside_one_to_seven_is_not_a_level_sfia_defines(bad: object) -> None:
    with pytest.raises(SfiaIdError):
        normalize_level(bad)


def test_slugs_are_ascii_lowercase_and_stable() -> None:
    assert slugify("Development and implementation") == "development-and-implementation"
    assert slugify("Réal-time/embedded") == "real-time-embedded"
    with pytest.raises(SfiaIdError):
        slugify("   ")


def test_a_subcategory_id_is_scoped_by_its_category() -> None:
    """A subcategory name is only unique within its category.

    An unscoped slug would MERGE two different groups into one node while every
    count still added up — the failure mode this repo has already hit twice.
    """
    a = subcategory_id("Development and implementation", "Systems development")
    b = subcategory_id("Change and transformation", "Systems development")
    assert a != b
    assert a.startswith(
        category_id("Development and implementation").replace("category", "subcategory")
    )


def test_a_related_link_outside_the_slice_resolves_to_nothing() -> None:
    codes = {"programming-software-development": "PROG"}
    assert skill_slug_to_code("programming-software-development", codes) == "PROG"
    # Not an error and not a new node: the caller drops the edge.
    assert skill_slug_to_code("something-not-loaded", codes) is None
