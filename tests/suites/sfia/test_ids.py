"""Unit tests for SFIA identity helpers (no Neo4j)."""

import pytest

from ta_taxonomies.suites.sfia.ids import (
    category_id,
    code_to_str,
    level_id,
    level_ref,
    level_source_id,
    level_to_int,
    skill_id,
    skill_ref,
    skill_url,
    slugify,
    subcategory_id,
)


def test_codes_stay_strings() -> None:
    assert code_to_str("PROG") == "PROG"
    assert code_to_str(" prog ") == "PROG"
    assert skill_id("PROG") == "sfia:skill:PROG"
    # SFIA's "ISCO" skill: a string code, scoped so it cannot meet ESCO's ISCO.
    assert skill_id("ISCO") == "sfia:skill:ISCO"
    with pytest.raises(ValueError):
        code_to_str("  ")


def test_level_ids() -> None:
    assert level_id("PROG", 4) == "sfia:level:PROG:4"
    assert level_id("TEST", "1") == "sfia:level:TEST:1"
    assert level_source_id("PROG", 4) == "PROG-4"


def test_levels_are_ints_in_range() -> None:
    assert level_to_int(7) == 7
    assert level_to_int(4.0) == 4
    for bad in (0, 8, 4.5, True):
        with pytest.raises(ValueError):
            level_to_int(bad)


def test_slugs_and_tree_ids() -> None:
    assert slugify("Strategy and planning") == "strategy-and-planning"
    assert slugify("Programming/software development") == "programming-software-development"
    assert category_id("strategy-and-architecture") == "sfia:category:strategy-and-architecture"
    assert subcategory_id("systems-development") == "sfia:subcategory:systems-development"


def test_pointers_not_payloads() -> None:
    assert skill_ref("PROG") == "SFIA 9 · PROG"
    assert level_ref("PROG", 4) == "SFIA 9 · PROG level 4"
    assert (
        skill_url("programming-software-development")
        == "https://sfia-online.org/en/sfia-9/skills/programming-software-development"
    )
