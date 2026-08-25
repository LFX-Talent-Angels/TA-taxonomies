"""Contract tools against the loaded BLS fixture (skipped if the DB is down)."""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest

from ta_taxonomies.contract import PolicyRef, Suite
from ta_taxonomies.suites.bls.config import (
    CONF_EXACT_ALT,
    CONF_EXACT_CODE,
    CONF_FOREIGN_SOC_CODE,
    POLICY_PROJECTED_GROWTH,
    POLICY_SHARE_BOTTLENECK,
    REL_BROADER_THAN,
    REL_EMPLOYED_IN,
)
from ta_taxonomies.suites.bls.db import neo4j_driver
from ta_taxonomies.suites.bls.load import run_load
from ta_taxonomies.suites.bls.tools import BlsSuite

pytestmark = pytest.mark.neo4j

SOFTWARE_DEVELOPERS = "bls:occupation:15-1252"
COMPUTER_PROGRAMMERS = "bls:occupation:15-1251"
REGISTERED_NURSES = "bls:occupation:29-1141"


@pytest.fixture(scope="module")
def suite() -> Iterator[BlsSuite]:
    run_load(mode="fixture", wipe=True)
    with neo4j_driver() as (driver, database):
        yield BlsSuite(driver, database=database)


# --- the contract ----------------------------------------------------------


def test_the_suite_exposes_the_contract_method_names(suite: BlsSuite) -> None:
    """isinstance against a runtime_checkable Protocol checks *names only*.

    It passes for an object whose methods take entirely different arguments and
    return entirely different types. Signatures are pinned by the test below;
    this one is worth keeping because it still catches a method going missing.
    """
    assert isinstance(suite, Suite)
    assert suite.name == "bls"


@pytest.mark.parametrize(
    "method", ["search_nodes", "get_neighbors", "enumerate_paths", "score_paths"]
)
def test_the_suite_matches_the_contract_signature(method: str) -> None:
    """The part isinstance cannot see.

    TA-agents calls these positionally and by keyword, so a drifted parameter
    name or a changed default is a break at the call site that the Protocol
    check would report as conformance.
    """
    assert inspect.signature(getattr(BlsSuite, method)) == inspect.signature(getattr(Suite, method))


# --- Locate ----------------------------------------------------------------


def test_locate_resolves_a_soc_code_to_one_occupation(suite: BlsSuite) -> None:
    result = suite.search_nodes("15-1252", kind="occupation")

    assert [c.node.id for c in result.candidates] == [SOFTWARE_DEVELOPERS]
    assert result.candidates[0].confidence == CONF_EXACT_CODE
    assert result.candidates[0].node.source == "bls"
    assert result.candidates[0].node.source_id == "15-1252"


def test_locate_resolves_the_unhyphenated_form_to_the_same_node(suite: BlsSuite) -> None:
    """oe.occupation writes 151252 and ep.occupation writes 15-1252.

    One code is one identity, so a caller holding either lands on one node.
    """
    assert suite.search_nodes("151252").candidates[0].node.id == SOFTWARE_DEVELOPERS


def test_locate_resolves_another_suites_identifier_onto_the_spine(suite: BlsSuite) -> None:
    """The thing this suite exists for, reachable through the ordinary tool.

    ``onet:occupation:15-1252.00`` is O*NET's id for an occupation built on SOC
    15-1252. Resolving it here is a string identity on the SOC code, reported
    under its own method and its own confidence so a caller can tell it apart
    from a hit on this suite's own code.
    """
    result = suite.search_nodes("onet:occupation:15-1252.00")

    assert [c.node.id for c in result.candidates] == [SOFTWARE_DEVELOPERS]
    assert result.candidates[0].method == "foreign_soc_code"
    assert result.candidates[0].confidence == CONF_FOREIGN_SOC_CODE
    assert result.meta["join"] == "soc_code_string"


def test_locate_resolves_a_published_lay_title(suite: BlsSuite) -> None:
    """BLS publishes 5,820 everyday job titles across 817 SOC codes.

    This is why the full-text index ships with the first load: the alias tier
    is `$q IN n.alt_labels`, which indexes the list rather than its elements and
    cannot use a range index.
    """
    result = suite.search_nodes("CCU Nurse")

    assert [c.node.id for c in result.candidates] == [REGISTERED_NURSES]
    assert result.candidates[0].method == "exact_alt"
    assert result.candidates[0].confidence == CONF_EXACT_ALT


def test_locate_never_invents_a_hit(suite: BlsSuite) -> None:
    result = suite.search_nodes("zzzz not an occupation zzzz")
    assert result.warnings == ["not_found"]
    assert result.candidates == []


def test_every_locate_result_names_the_confidence_policy(suite: BlsSuite) -> None:
    """Three suites' confidences are on three unreconciled scales.

    Without the policy name a caller merging candidate lists sees bare floats
    and no way to tell — sorting a combined list by confidence is a bug waiting
    to be written.
    """
    for query in ("15-1252", "zzzz not an occupation zzzz"):
        assert suite.search_nodes(query).meta["confidence_policy"]["name"] == (
            "bls-locate-confidence"
        )


# --- the spine -------------------------------------------------------------


def test_resolve_soc_returns_the_node_and_its_whole_roll_up(suite: BlsSuite) -> None:
    result = suite.resolve_soc("onet:soc:29-1141")

    assert result.nodes[0].id == REGISTERED_NURSES
    # 29-1000, not 29-1100: the minor group is looked up in the published set,
    # and zeroing the last two digits would name a code SOC does not define.
    assert result.meta["rollup"]["minor"] == "29-1000"
    assert result.meta["rollup"]["major"] == "29-0000"
    assert result.meta["join"] == "soc_code_string"
    assert {edge.type for edge in result.edges} == {REL_BROADER_THAN}


def test_resolve_soc_reports_the_onet_extension_it_dropped(suite: BlsSuite) -> None:
    """29-1141.01 is Acute Care Nurses; SOC has one line, 29-1141.

    Collapsing to it is correct, but the caller asked about a subdivision that
    does not exist here and should not have to infer that from the id.
    """
    result = suite.resolve_soc("onet:occupation:29-1141.01")

    assert result.nodes[0].id == REGISTERED_NURSES
    assert "onetsoc_extension_dropped" in result.warnings
    assert result.meta["onetsoc_extension_dropped"] is True


def test_resolve_soc_answers_no_link_for_an_identifier_with_no_soc_code(
    suite: BlsSuite,
) -> None:
    """ESCO is ISCO-aligned. ARCHITECTURE.md: the answer is "no link", recorded.

    Bridging that gap needs a published crosswalk stored and cited in
    ``crosswalks/``, which is not this suite's to invent.
    """
    result = suite.resolve_soc("esco:occupation:f2b15a0e-e65a-438a-affb-29b9d50b77d1")

    assert result.warnings == ["no_soc_code"]
    assert result.nodes == []
    assert "crosswalks/" in result.meta["next"]


def test_resolve_soc_distinguishes_a_valid_absent_code_from_a_bad_one(
    suite: BlsSuite,
) -> None:
    """BLS publishes data for 825 of the 867 detailed codes in the 2018 SOC.

    So a well-formed code with no node is a real answer, and it is a different
    answer from "that is not a SOC code".
    """
    result = suite.resolve_soc("99-9999")

    assert result.warnings == ["not_found"]
    assert result.meta["soc_code"] == "99-9999"


# --- Connect ---------------------------------------------------------------


def test_connect_refuses_a_skills_question_instead_of_answering_it_empty(
    suite: BlsSuite,
) -> None:
    """An empty list is a valid answer, so nothing anywhere would flag it.

    TA-agents' phrase router defaults to a skills question, and BLS has no
    skills layer, so the honest response is "this suite cannot answer that"
    with a pointer to one that can.
    """
    result = suite.get_neighbors(SOFTWARE_DEVELOPERS, rel_types=["HAS_SKILL"])

    assert result.warnings == ["unknown_rel_types:['HAS_SKILL']"]
    assert result.edges == []
    assert "ADR-0006" in result.meta["no_skills_layer"]


def test_connect_returns_the_industries_that_employ_an_occupation(
    suite: BlsSuite,
) -> None:
    result = suite.get_neighbors(SOFTWARE_DEVELOPERS, rel_types=[REL_EMPLOYED_IN])

    assert result.edges
    assert all(edge.type == REL_EMPLOYED_IN for edge in result.edges)
    assert all(
        edge.properties.get("industry_share_of_occupation_base") is not None
        for edge in result.edges
    )


def test_connect_warns_that_the_employment_matrix_overlaps(suite: BlsSuite) -> None:
    """Summary and detail cells coexist by design, so summing double-counts.

    A caller who does not know that gets a plausible wrong number, which is
    worse than an error.
    """
    result = suite.get_neighbors(SOFTWARE_DEVELOPERS, rel_types=[REL_EMPLOYED_IN])

    assert result.meta["employment_matrix"]["overlapping"] is True
    assert result.meta["employment_matrix"]["filter_on"] == [
        "occupation_type",
        "industry_type",
    ]


def test_connect_reports_a_missing_node_differently_from_an_isolated_one(
    suite: BlsSuite,
) -> None:
    assert suite.get_neighbors("bls:occupation:99-9999").warnings == ["node_not_found"]


# --- Pathfind and Evaluate -------------------------------------------------


def test_pathfind_routes_two_occupations_through_a_shared_industry(
    suite: BlsSuite,
) -> None:
    result = suite.enumerate_paths(
        SOFTWARE_DEVELOPERS, COMPUTER_PROGRAMMERS, max_depth=2, max_paths=20
    )

    assert result.paths
    assert result.pruning is not None
    assert result.pruning.considered == result.pruning.returned + result.pruning.pruned
    for path in result.paths:
        assert path.node_ids[0] == SOFTWARE_DEVELOPERS
        assert path.node_ids[-1] == COMPUTER_PROGRAMMERS


def test_the_growth_policy_ranks_differently_from_the_share_policies(
    suite: BlsSuite,
) -> None:
    """The claim this suite makes for itself, checked against the real graph.

    Where the jobs are and where they are going are different questions, and
    the two policies are only worth having separately if they disagree.
    """
    found = suite.enumerate_paths(
        SOFTWARE_DEVELOPERS, COMPUTER_PROGRAMMERS, max_depth=2, max_paths=20
    )
    by_share = suite.score_paths(found.paths, POLICY_SHARE_BOTTLENECK)
    by_growth = suite.score_paths(found.paths, POLICY_PROJECTED_GROWTH)

    share_order = [tuple(s.path.node_ids) for s in by_share.scored_paths]
    growth_order = [tuple(s.path.node_ids) for s in by_growth.scored_paths]
    assert sorted(share_order) == sorted(growth_order)
    assert share_order != growth_order


def test_an_unknown_policy_is_refused_against_a_real_graph(suite: BlsSuite) -> None:
    found = suite.enumerate_paths(
        SOFTWARE_DEVELOPERS, COMPUTER_PROGRAMMERS, max_depth=2, max_paths=5
    )
    result = suite.score_paths(found.paths, PolicyRef(name="bls-invented", version="1"))

    assert result.warnings[0] == "unknown_policy:bls-invented"
    assert result.scored_paths == []
