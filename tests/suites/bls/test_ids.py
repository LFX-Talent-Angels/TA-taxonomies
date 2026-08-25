"""SOC identity and hierarchy — pure functions, no Neo4j.

These are the tests that matter most in this suite, because the SOC hierarchy
is *derived from the code* rather than read from a join table. If
``soc_ancestors`` is wrong, every roll-up is wrong and nothing else notices:
the graph loads, the counts add up, and the answers are quietly one level off.
"""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.bls.ids import (
    BlsIdError,
    NoSocCodeError,
    dedupe_titles,
    industry_id,
    is_detailed_occupation,
    nearest_present_parent,
    normalize_industry_code,
    normalize_soc_code,
    occupation_id,
    resolve_soc_minor,
    soc_ancestors,
    soc_broad_code,
    soc_code_from_any,
    soc_group_id,
    soc_level,
    soc_major_code,
    soc_minor_candidates,
    soc_node_id,
    soc_parent_chain,
)

# The minor groups BLS publishes for these two major groups. Both shapes are
# represented on purpose: 29-1000 has three trailing zeros and 15-1200 has two,
# and everything below turns on the difference.
PUBLISHED_MINORS = frozenset({"11-1000", "15-1200", "15-2000", "29-1000", "29-2000", "29-9000"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15-1252", "15-1252"),
        ("151252", "15-1252"),  # oe.occupation writes it unhyphenated
        ("  15-1252  ", "15-1252"),
        ("00-0000", "00-0000"),
        ("11-1011", "11-1011"),
    ],
)
def test_soc_codes_normalize_to_the_published_hyphenated_form(raw: str, expected: str) -> None:
    assert normalize_soc_code(raw) == expected


def test_a_soc_code_is_never_read_as_a_number() -> None:
    """The leading zeros are the code, not formatting.

    ``00-0000`` through a float is ``0.0``; ``11-1011`` is ``11 - 1011``.
    Neither is recoverable, and both would silently match the wrong occupation.
    """
    assert normalize_soc_code("00-0000") == "00-0000"
    # A six-digit code that survived a float round trip is recovered, which is
    # what code_to_str is for.
    assert normalize_soc_code(151252.0) == "15-1252"
    # One that did not survive fails loudly instead of resolving to some other
    # occupation: 00-0000 through a float is 0.0, and "0" is not a SOC code.
    with pytest.raises(BlsIdError):
        normalize_soc_code(0.0)


@pytest.mark.parametrize("raw", ["", None, "15-125", "1512520", "abc", "15_1252"])
def test_a_malformed_soc_code_is_refused_rather_than_repaired(raw: object) -> None:
    with pytest.raises(BlsIdError):
        normalize_soc_code(raw)


@pytest.mark.parametrize(
    ("code", "level"),
    [
        ("11-0000", "major"),
        ("11-1000", "minor"),  # a minor group that happens to have three zeros
        ("15-1200", "minor"),  # …and one that has two. Both are real.
        ("11-1010", "broad"),
        ("15-1250", "broad"),
        ("11-1011", "detailed"),
        ("00-0000", "major"),
        ("15-1252", "detailed"),
    ],
)
def test_soc_level_is_read_off_the_digits(code: str, level: str) -> None:
    """The minor group is the last *two* digits zeroed, not the last three.

    ``11-1000`` and ``15-1200`` are both minor groups in SOC 2018. Reading the
    rule off the first alone makes ``15-1200`` a broad group whose parent is
    ``15-1000`` — a code SOC does not define — and every roll-up above the
    broad level lands one level too high while the load validates clean.
    """
    assert soc_level(code) == level


def test_the_broad_and_major_levels_are_derivable_from_the_code() -> None:
    """These two are uniform throughout SOC, so no lookup is needed."""
    assert soc_broad_code("15-1252") == "15-1250"
    assert soc_broad_code("15-1250") is None  # already a broad group
    assert soc_major_code("15-1252") == "15-0000"
    assert soc_major_code("15-0000") is None


def test_the_minor_group_is_not_derivable_and_the_api_says_so() -> None:
    """The single most consequential fact in this module.

    Minor groups come in two shapes and both are real. 29-1210 (Physicians)
    sits under 29-1000; 15-1250 (Software and Web Developers) sits under
    15-1200. Zeroing the last two digits — which the 92-out-of-95 majority
    makes look like the rule — would put Physicians under 29-1200, a code SOC
    does not define, and the load would validate clean with every roll-up above
    the broad level one level wrong.
    """
    assert soc_minor_candidates("29-1210") == ["29-1200", "29-1000"]
    assert soc_minor_candidates("15-1250") == ["15-1200", "15-1000"]
    # When the third digit is already zero both candidates coincide, which is
    # why the majority shape hides the problem.
    assert soc_minor_candidates("11-1011") == ["11-1000"]

    assert resolve_soc_minor("29-1210", PUBLISHED_MINORS) == "29-1000"
    assert resolve_soc_minor("15-1250", PUBLISHED_MINORS) == "15-1200"
    assert resolve_soc_minor("11-1011", PUBLISHED_MINORS) == "11-1000"


def test_an_unresolvable_minor_group_is_none_rather_than_an_invented_code() -> None:
    """An invented ancestor is indistinguishable from a real one once it is a node."""
    assert resolve_soc_minor("29-1210", frozenset()) is None
    assert soc_ancestors("29-1210", frozenset()) == {"major": "29-0000"}


def test_ancestors_walk_the_published_coding_structure() -> None:
    assert soc_ancestors("15-1252", PUBLISHED_MINORS) == {
        "broad": "15-1250",
        "minor": "15-1200",
        "major": "15-0000",
    }
    assert soc_ancestors("15-1250", PUBLISHED_MINORS) == {
        "minor": "15-1200",
        "major": "15-0000",
    }
    assert soc_ancestors("15-1200", PUBLISHED_MINORS) == {"major": "15-0000"}
    assert soc_ancestors("15-0000", PUBLISHED_MINORS) == {}
    # The other minor-group spelling, which is where the rule gets tested.
    assert soc_ancestors("11-1011", PUBLISHED_MINORS) == {
        "broad": "11-1010",
        "minor": "11-1000",
        "major": "11-0000",
    }
    # And the case a "zero the last two digits" rule gets wrong.
    assert soc_ancestors("29-1211", PUBLISHED_MINORS) == {
        "broad": "29-1210",
        "minor": "29-1000",
        "major": "29-0000",
    }


def test_no_code_is_ever_its_own_ancestor() -> None:
    """A self-parent would make the BROADER_THAN tree cyclic.

    The arithmetic makes it look impossible, which is exactly why it is worth
    an assertion: every level's derivation zeroes digits the level below still
    holds, so the only way to get here is a change to soc_level.
    """
    for code in (
        "00-0000",
        "11-0000",
        "11-1000",
        "11-1010",
        "11-1011",
        "15-0000",
        "15-1200",
        "15-1250",
        "15-1252",
    ):
        assert code not in soc_ancestors(code, PUBLISHED_MINORS).values()
        assert code not in soc_parent_chain(code, PUBLISHED_MINORS)


def test_the_parent_chain_is_ordered_nearest_first() -> None:
    assert soc_parent_chain("15-1252", PUBLISHED_MINORS) == ["15-1250", "15-1200", "15-0000"]


def test_the_nearest_present_parent_skips_levels_bls_does_not_publish() -> None:
    """BLS publishes a line for 174 of the 450 broad groups its codes imply.

    The edge therefore lands on whichever ancestor exists, and roll-up does not
    depend on that choice — the node's own soc_broad/soc_minor/soc_major
    properties are complete either way.
    """
    full = {"15-1250", "15-1200", "15-0000"}
    assert nearest_present_parent("15-1252", full, PUBLISHED_MINORS) == "15-1250"

    no_broad = {"15-1200", "15-0000"}
    assert nearest_present_parent("15-1252", no_broad, PUBLISHED_MINORS) == "15-1200"

    assert nearest_present_parent("15-1252", set(), PUBLISHED_MINORS) is None


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("15-1252", "15-1252"),
        ("151252", "15-1252"),
        ("15-1252.00", "15-1252"),
        ("bls:occupation:15-1252", "15-1252"),
        ("bls:soc:15-1200", "15-1200"),
        ("onet:soc:15-1252", "15-1252"),
        ("onet:occupation:15-1252.00", "15-1252"),
        ("onet:occupation:29-1141.01", "29-1141"),
    ],
)
def test_a_soc_code_is_found_in_whatever_identifier_carries_it(
    identifier: str, expected: str
) -> None:
    """This is the join the whole crosswalk axis rests on.

    It is a *string identity on the SOC code*: O*NET builds its codes by
    appending an extension to a published SOC code, so the prefix is that code.
    Not a semantic mapping, and resolve_soc says so in its result.
    """
    assert soc_code_from_any(identifier) == expected


@pytest.mark.parametrize(
    "identifier",
    [
        "http://data.europa.eu/esco/occupation/f2b15a0e-e65a-438a-affb-29b9d50b77d1",
        "esco:occupation:f2b15a0e-e65a-438a-affb-29b9d50b77d1",
        "sfia:skill:PROG",
        "2512.4",  # an ISCO code — four digits, and not a SOC code
        "",
        None,
    ],
)
def test_an_identifier_with_no_soc_code_is_a_recorded_absence(identifier: object) -> None:
    """ARCHITECTURE.md: where no reliable link exists, the answer is "no link".

    ESCO is ISCO-aligned and carries no SOC code. Raising a *distinct*
    exception is what lets resolve_soc report ``no_soc_code`` rather than
    ``malformed`` — the caller's identifier is fine; it simply has no SOC in it.
    """
    with pytest.raises(NoSocCodeError):
        soc_code_from_any(identifier)


def test_the_no_link_error_is_distinguishable_from_a_corrupt_read() -> None:
    """NoSocCodeError is a BlsIdError, so a caller can catch either deliberately."""
    assert issubclass(NoSocCodeError, BlsIdError)


def test_ids_are_suite_scoped_and_split_by_what_the_code_is() -> None:
    assert occupation_id("15-1252") == "bls:occupation:15-1252"
    assert soc_group_id("15-1200") == "bls:soc:15-1200"
    assert industry_id("5415A1") == "bls:industry:5415A1"

    # A group id for a detailed code, or the reverse, would put two kinds of
    # thing under one id space and make the label a guess at query time.
    with pytest.raises(BlsIdError):
        occupation_id("15-1200")
    with pytest.raises(BlsIdError):
        soc_group_id("15-1252")


def test_soc_node_id_picks_the_kind_a_caller_does_not_know_yet() -> None:
    """Every crosswalk is in this position: the level is a property of the code."""
    assert soc_node_id("15-1252") == "bls:occupation:15-1252"
    assert soc_node_id("15-1250") == "bls:soc:15-1250"
    assert is_detailed_occupation("15-1252")
    assert not is_detailed_occupation("15-1250")


@pytest.mark.parametrize("code", ["1131-2", "3250A1", "61110L", "TE1000", "110000"])
def test_real_nem_industry_codes_are_accepted_as_published(code: str) -> None:
    """Four of these are not six digits. A tighter rule would reject the source."""
    assert normalize_industry_code(code) == code


@pytest.mark.parametrize("code", ["", None, "with space", "  "])
def test_an_unrepresentable_industry_code_fails_loudly(code: object) -> None:
    with pytest.raises(BlsIdError):
        normalize_industry_code(code)


def test_lay_titles_are_deduped_in_source_order_with_the_null_removed() -> None:
    """``-`` is BLS's null in these columns, not a job anyone holds."""
    assert dedupe_titles(["Coder", "-", "Coder", " Programmer ", ""]) == ["Coder", "Programmer"]
    assert dedupe_titles(None) == []
    assert dedupe_titles("Coder") == ["Coder"]
