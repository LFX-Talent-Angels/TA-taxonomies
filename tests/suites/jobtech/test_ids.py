"""Unit tests for JobTech identity helpers (no Neo4j)."""

import pytest

from ta_taxonomies.suites.jobtech.ids import (
    JobtechIdError,
    codes_in_query,
    concept_id,
    headline_id,
    occupation_id,
    skill_id,
    ssyk_code,
    ssyk_id,
)


def test_ids() -> None:
    assert occupation_id("5qT8_z9d_8rw") == "jobtech:occupation:5qT8_z9d_8rw"
    assert skill_id(" UMGQ_Hmq_ccz ") == "jobtech:skill:UMGQ_Hmq_ccz"
    assert headline_id("abcd_efg_hij") == "jobtech:skill-headline:abcd_efg_hij"
    assert ssyk_id("7612") == "jobtech:ssyk:7612"
    assert ssyk_id(7) == "jobtech:ssyk:7"


@pytest.mark.parametrize(
    "bad", ["", None, "5qT8_z9d", "5qT8-z9d-8rw", "x' OR 1=1", "../..", "5qT8_z9d_8rw!"]
)
def test_bad_concept_ids(bad: object) -> None:
    with pytest.raises(JobtechIdError):
        concept_id(bad)


@pytest.mark.parametrize("bad", ["", "12345", "76a2", "7612; MATCH", None])
def test_bad_ssyk_codes(bad: object) -> None:
    with pytest.raises(JobtechIdError):
        ssyk_code(bad)


def test_codes_in_query() -> None:
    assert codes_in_query("7612") == ["7612"]
    for named in ("SSYK 7612", "ssyk-kod 7612", "SSYK: 7612", "SSYK 2012 7612"):
        assert codes_in_query(named) == ["7612"], named
    assert codes_in_query("SSYK bagare") == []
    assert codes_in_query(" 5qT8_z9d_8rw ") == ["5qT8_z9d_8rw"]
    for text in ("bagare", "76123", "5qT8_z9d_8r", ""):
        assert codes_in_query(text) == []
