"""Unit tests for O*NET identity helpers (no Neo4j)."""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.onet.ids import (
    OnetIdError,
    code_to_str,
    dedupe_titles,
    element_ancestors,
    element_id,
    element_parent_id,
    is_detailed_occupation,
    normalize_element_id,
    normalize_onetsoc_code,
    occupation_id,
    soc_code_from_onetsoc,
    soc_group_id,
    task_id,
)


def test_onetsoc_code_keeps_its_exact_published_form() -> None:
    # The trailing ".00" is part of the identifier, not a decimal that can be
    # normalised away, and the leading zero of "01-1011.00" is load-bearing.
    assert normalize_onetsoc_code("15-1252.00") == "15-1252.00"
    assert normalize_onetsoc_code(" 29-1141.00 ") == "29-1141.00"
    assert occupation_id("15-1252.00") == "onet:occupation:15-1252.00"


@pytest.mark.parametrize("bad", ["15-1252", "15-1252.0", "151252.00", "", "Software Developers"])
def test_malformed_codes_are_refused_not_repaired(bad: str) -> None:
    with pytest.raises(OnetIdError):
        normalize_onetsoc_code(bad)


def test_soc_code_is_the_published_prefix() -> None:
    assert soc_code_from_onetsoc("15-1252.00") == "15-1252"
    assert soc_code_from_onetsoc("11-1011.03") == "11-1011"
    assert soc_group_id("15-1252.00") == "onet:soc:15-1252"
    assert soc_group_id("15-1252") == "onet:soc:15-1252"


def test_detailed_occupations_are_the_non_zero_extensions() -> None:
    assert not is_detailed_occupation("15-1252.00")
    assert is_detailed_occupation("11-1011.03")


def test_element_ids_carry_their_own_hierarchy() -> None:
    assert element_id("2.B.3.e") == "onet:element:2.B.3.e"
    assert element_parent_id("2.B.3.e") == "2.B.3"
    assert element_parent_id("2") is None
    assert element_ancestors("1.A.1.a.1") == ["1.A.1.a", "1.A.1", "1.A", "1"]


def test_element_id_rejects_non_identifiers() -> None:
    with pytest.raises(OnetIdError):
        normalize_element_id("2.B.3 e")
    with pytest.raises(OnetIdError):
        normalize_element_id("")


def test_task_ids_stay_strings() -> None:
    assert task_id("21662") == "onet:task:21662"
    assert task_id(21662) == "onet:task:21662"
    with pytest.raises(OnetIdError):
        task_id("21662a")


def test_code_to_str_survives_spreadsheet_typing() -> None:
    # Crosswalk workbooks type SOC-like values as numbers; the string form has
    # to come back intact rather than as "2051.0".
    assert code_to_str(2051.0) == "2051"
    assert code_to_str("15-1252.00") == "15-1252.00"
    assert code_to_str(None) is None
    assert code_to_str("  ") is None


def test_dedupe_titles_drops_onet_nulls_and_keeps_order() -> None:
    assert dedupe_titles(["Coder", "n/a", "Coder", " Dev ", ""]) == ["Coder", "Dev"]
    assert dedupe_titles(None) == []
    assert dedupe_titles("Solo Title") == ["Solo Title"]
