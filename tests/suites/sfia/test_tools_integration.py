"""SfiaSuite against the loaded fixture (skipped if Neo4j is down)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ta_taxonomies.suites.sfia.config import (
    CONF_EXACT_CODE,
    CONF_EXACT_PREF,
    POLICY_LEVEL_BOTTLENECK,
    POLICY_LEVEL_PEAK,
    REL_HAS_LEVEL,
    REL_RELATED_TO,
)
from ta_taxonomies.suites.sfia.db import neo4j_driver
from ta_taxonomies.suites.sfia.load import run_load
from ta_taxonomies.suites.sfia.tools import SfiaSuite

pytestmark = pytest.mark.neo4j


@pytest.fixture(scope="module")
def suite() -> Iterator[SfiaSuite]:
    run_load(mode="fixture", wipe=True)
    with neo4j_driver() as (driver, database):
        yield SfiaSuite(driver, database=database)


def test_a_four_letter_code_resolves_as_identity(suite: SfiaSuite) -> None:
    result = suite.search_nodes("PROG")
    assert [c.node.id for c in result.candidates] == ["sfia:skill:PROG"]
    assert result.candidates[0].method == "exact_code"
    assert result.candidates[0].confidence == CONF_EXACT_CODE


def test_the_isco_code_resolves_to_the_sfia_skill_and_says_whose_namespace(
    suite: SfiaSuite,
) -> None:
    """ADR-0006 §5's named trap, exercised.

    SFIA's ``ISCO`` is *Information systems coordination*. The ILO's ISCO that
    ESCO aligns to is an occupation classification. Four identical characters,
    nothing else in common — so the answer has to name the namespace it came
    from, and the id has to carry it.
    """
    result = suite.search_nodes("ISCO")
    node = result.candidates[0].node
    assert node.id == "sfia:skill:ISCO"
    assert node.source == "sfia"
    assert node.source_id == "ISCO"
    assert node.label == "Information systems coordination"
    assert result.meta["code_scope"] == "sfia-skill-code"


def test_a_lowercase_code_resolves_because_that_is_how_people_type(suite: SfiaSuite) -> None:
    assert suite.search_nodes("prog").candidates[0].node.id == "sfia:skill:PROG"


def test_a_four_letter_word_that_is_not_a_code_falls_through_to_the_name_tiers(
    suite: SfiaSuite,
) -> None:
    """The code screen is syntactic, so a miss must not dead-end.

    ``time`` is screened in as a possible code, the seek finds nothing, and the
    name tiers then match *Real-time/embedded systems development*.
    """
    result = suite.search_nodes("time")
    assert result.candidates, result.warnings
    assert result.candidates[0].method != "exact_code"
    assert "sfia:skill:RESD" in {c.node.id for c in result.candidates}


def test_a_four_letter_english_word_can_also_be_a_real_code(suite: SfiaSuite) -> None:
    """Which is why the code tier is first rather than last.

    ``TEST`` is *Functional testing*, and ``PORT`` is *Software configuration*.
    If the code tier ran after the substring tier, typing a code would return
    every skill whose name happens to contain those four letters, with the
    identity the user actually typed somewhere in the list.
    """
    for code, expected in (("TEST", "sfia:skill:TEST"), ("PORT", "sfia:skill:PORT")):
        result = suite.search_nodes(code)
        assert result.candidates[0].node.id == expected
        assert result.candidates[0].method == "exact_code"


def test_an_exact_name_resolves(suite: SfiaSuite) -> None:
    result = suite.search_nodes("Software design")
    assert [c.node.id for c in result.candidates] == ["sfia:skill:SWDN"]
    assert result.candidates[0].confidence == CONF_EXACT_PREF


def test_a_case_insensitive_name_resolves_at_lower_confidence(suite: SfiaSuite) -> None:
    result = suite.search_nodes("software DESIGN")
    assert [c.node.id for c in result.candidates] == ["sfia:skill:SWDN"]
    assert result.candidates[0].method == "casefold_pref"
    assert result.candidates[0].confidence < CONF_EXACT_PREF


def test_a_substring_reaches_several_skills_and_says_so(suite: SfiaSuite) -> None:
    result = suite.search_nodes("software")
    ids = {c.node.id for c in result.candidates}
    assert {"sfia:skill:SWDN", "sfia:skill:PORT"} <= ids
    assert "ambiguous" in result.warnings


def test_there_is_no_alias_tier_to_reach(suite: SfiaSuite) -> None:
    """SFIA publishes one name per skill; any synonym would be one we wrote."""
    result = suite.search_nodes("coder")
    assert result.warnings[0] == "not_found"
    assert not result.candidates


def test_a_level_is_searchable_as_a_first_class_node(suite: SfiaSuite) -> None:
    result = suite.search_nodes("Level 6", kind="level")
    node = result.candidates[0].node
    assert node.id == "sfia:level:6"
    assert node.properties["level"] == 6


def test_an_unknown_kind_is_refused(suite: SfiaSuite) -> None:
    assert suite.search_nodes("PROG", kind="occupation").warnings == ["unknown_kind:occupation"]


def test_a_skill_node_points_at_the_definition_the_graph_does_not_store(
    suite: SfiaSuite,
) -> None:
    node = suite.search_nodes("PROG").candidates[0].node
    assert node.properties["source_url"].endswith("/skills/programming-software-development")
    assert not any("descri" in key.lower() for key in node.properties)


def test_neighbours_carry_levels_and_related_skills(suite: SfiaSuite) -> None:
    result = suite.get_neighbors("sfia:skill:PROG")
    types = {edge.type for edge in result.edges}
    assert REL_HAS_LEVEL in types
    assert REL_RELATED_TO in types
    levels = sorted(edge.properties["level"] for edge in result.edges if edge.type == REL_HAS_LEVEL)
    assert levels == [2, 3, 4, 5, 6]


def test_connect_says_out_loud_that_it_has_no_essential_flag(suite: SfiaSuite) -> None:
    """The silent-empty-answer trap, made loud.

    TA-agents keeps only neighbours whose ``relation_type`` matches, defaulting
    to "essential". Against this graph that filter matches nothing and the agent
    reports no skills — silently, because an empty list is a valid answer.
    """
    result = suite.get_neighbors("sfia:skill:PROG")
    assert "relation_type_absent" in result.warnings
    assert result.meta["relation_type_policy"]["name"] == "sfia-no-essential-projection"
    assert all("relation_type" not in edge.properties for edge in result.edges)


def test_a_missing_node_is_named_rather_than_returned_empty(suite: SfiaSuite) -> None:
    assert suite.get_neighbors("sfia:skill:ZZZZ").warnings == ["node_not_found"]


def test_two_skills_are_connected_through_the_level_they_share(suite: SfiaSuite) -> None:
    """The route only this suite can offer: shared responsibility.

    PROG runs 2–6 and ISCO 6–7, so level 6 is where a programmer and an
    information-systems coordinator meet. ESCO and O*NET have no such axis.
    """
    result = suite.enumerate_paths("sfia:skill:PROG", "sfia:skill:ISCO", max_depth=2)
    assert result.paths, result.warnings
    assert any("sfia:level:6" in path.node_ids for path in result.paths)


def test_the_branching_cap_does_not_hide_the_answer(suite: SfiaSuite) -> None:
    """Levels are extreme hubs; a level's kept neighbours are almost never the target.

    Without the arrival exemption, Pathfind between two skills returns nothing
    while reporting a large, healthy-looking pruned count.
    """
    result = suite.enumerate_paths("sfia:skill:PROG", "sfia:skill:ISCO", max_depth=2)
    assert result.pruning is not None
    assert result.pruning.returned > 0
    assert result.pruning.considered == result.pruning.returned + result.pruning.pruned


def test_a_path_to_nowhere_is_reported_as_such(suite: SfiaSuite) -> None:
    result = suite.enumerate_paths("sfia:skill:PROG", "sfia:skill:ZZZZ")
    assert result.warnings == ["endpoint_not_found"]


def test_scoring_real_paths_ranks_by_responsibility(suite: SfiaSuite) -> None:
    found = suite.enumerate_paths("sfia:skill:PROG", "sfia:skill:ISCO", max_depth=2, max_paths=20)
    peak = suite.score_paths(found.paths, POLICY_LEVEL_PEAK)
    bottleneck = suite.score_paths(found.paths, POLICY_LEVEL_BOTTLENECK)

    assert peak.scored_paths and bottleneck.scored_paths
    assert all(0.0 <= item.score <= 1.0 for item in peak.scored_paths)
    # Every route between these two runs through a level they both reach, so
    # peak and bottleneck agree here — worth asserting at that strength rather
    # than claiming a divergence the data does not show.
    assert peak.scored_paths[0].score == bottleneck.scored_paths[0].score
    assert peak.meta["level_scale"] == [1, 7]
