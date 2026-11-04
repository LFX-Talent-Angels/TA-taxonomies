"""Unit tests for BLS identity helpers (no Neo4j)."""

import pytest

from ta_taxonomies.suites.bls.ids import (
    BlsIdError,
    codes_in_query,
    education_id,
    group_id,
    occupation_id,
    parent_codes,
    skill_id,
    slugify,
    soc_code,
    soc_level,
    training_id,
)


def test_occupation_and_group_ids() -> None:
    assert occupation_id("15-1252") == "bls:occupation:15-1252"
    assert occupation_id(" 15-1252 ") == "bls:occupation:15-1252"
    assert group_id("15-1250") == "bls:soc-group:15-1250"


@pytest.mark.parametrize(
    "bad",
    ["", "  ", None, "15-125", "151252", "15-1252.00", "x' OR 1=1", "../..", "15-1252; MATCH"],
)
def test_bad_codes_are_refused(bad: object) -> None:
    with pytest.raises(BlsIdError):
        soc_code(bad)
    with pytest.raises(BlsIdError):
        occupation_id(bad)


@pytest.mark.parametrize(
    ("code", "level"),
    [
        ("15-0000", "major"),
        ("15-1200", "minor"),
        ("11-9100", "minor"),
        ("15-1250", "broad"),
        ("31-1120", "broad"),
        ("15-1252", "detailed"),
        ("11-9199", "detailed"),
    ],
)
def test_soc_level_is_in_the_digits(code: str, level: str) -> None:
    assert soc_level(code) == level


def test_parent_codes() -> None:
    assert parent_codes("15-1252") == ("15-1250", "15-1200", "15-0000")
    assert parent_codes("29-1161") == ("29-1160", "29-1100", "29-0000")


def test_category_ids_are_slugs() -> None:
    assert skill_id("Critical and analytical thinking") == (
        "bls:skill:critical-and-analytical-thinking"
    )
    assert education_id("Bachelor's degree") == "bls:education:bachelor-s-degree"
    assert training_id("Short-term on-the-job training") == (
        "bls:training:short-term-on-the-job-training"
    )
    with pytest.raises(BlsIdError):
        slugify("—")


def test_codes_in_query() -> None:
    assert codes_in_query("15-1252") == ["15-1252"]
    # An O*NET-SOC code names the same SOC occupation.
    assert codes_in_query("15-1252.00") == ["15-1252"]
    assert codes_in_query(" 15-1252 ") == ["15-1252"]
    for not_a_code in ("software developers", "15-125", "15-1252.0", "15-1252x", ""):
        assert codes_in_query(not_a_code) == []
