"""Contract tools against the loaded O*NET fixture (skipped if the DB is down)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ta_taxonomies.contract import PolicyRef, Suite
from ta_taxonomies.suites.onet.config import (
    CONF_EXACT_CODE,
    POLICY_BOTTLENECK,
    POLICY_LOWER_CI,
    POLICY_MEAN,
)
from ta_taxonomies.suites.onet.db import neo4j_driver
from ta_taxonomies.suites.onet.load import run_load
from ta_taxonomies.suites.onet.tools import OnetSuite

pytestmark = pytest.mark.neo4j

SOFTWARE_DEVELOPERS = "onet:occupation:15-1252.00"
DATA_SCIENTISTS = "onet:occupation:15-2051.00"
PROGRAMMING = "onet:element:2.B.3.e"


@pytest.fixture(scope="module")
def suite() -> Iterator[OnetSuite]:
    run_load(mode="fixture", wipe=True)
    with neo4j_driver() as (driver, database):
        yield OnetSuite(driver, database=database)


def test_the_suite_satisfies_the_shared_contract(suite: OnetSuite) -> None:
    assert isinstance(suite, Suite)
    assert suite.name == "onet"


def test_locate_resolves_a_code_to_exactly_one_occupation(suite: OnetSuite) -> None:
    result = suite.search_nodes("15-1252.00", kind="occupation")

    assert [c.node.id for c in result.candidates] == [SOFTWARE_DEVELOPERS]
    assert result.candidates[0].confidence == CONF_EXACT_CODE
    assert result.candidates[0].node.source == "onet"
    assert result.candidates[0].node.source_id == "15-1252.00"


def test_locate_resolves_a_lay_job_title_through_the_alias_pool(suite: OnetSuite) -> None:
    # The reason the full-text index ships with the first load: real users type
    # what they call themselves, and O*NET publishes 57k of those.
    result = suite.search_nodes("Java Developer", kind="occupation")

    assert result.candidates
    assert result.candidates[0].method == "exact_alt"
    assert result.candidates[0].node.kind == "OnetOccupation"


def test_locate_reports_a_miss_rather_than_inventing_one(suite: OnetSuite) -> None:
    result = suite.search_nodes("zzz not an occupation zzz")

    assert result.candidates == []
    assert "not_found" in result.warnings


def test_connect_returns_weighted_edges_and_names_the_binary_policy(suite: OnetSuite) -> None:
    result = suite.get_neighbors(SOFTWARE_DEVELOPERS, ["HAS_SKILL"])

    assert len(result.edges) == 120
    assert all(edge.type == "HAS_SKILL" for edge in result.edges)
    assert all(edge.properties.get("importance") is not None for edge in result.edges)
    # essential/optional is a projection of Importance, so the result has to
    # say which policy produced it.
    assert result.meta["relation_type_policy"]["name"] == "onet-essential-threshold"
    kinds = {edge.properties.get("relation_type") for edge in result.edges}
    assert kinds <= {"essential", "optional"}


def test_connect_rejects_a_relationship_type_this_suite_does_not_traverse(
    suite: OnetSuite,
) -> None:
    # The Sprint 1 prototype called this edge REQUIRES_SKILL. Every caller in
    # TA-agents asks for HAS_SKILL, so the canonical name is the only one that
    # answers.
    assert suite.get_neighbors(SOFTWARE_DEVELOPERS, ["REQUIRES_SKILL"]).warnings == [
        "unknown_rel_types:['REQUIRES_SKILL']"
    ]
    assert suite.get_neighbors(SOFTWARE_DEVELOPERS, ["HAS_SKILL"]).edges


def test_pathfind_reaches_the_target_even_through_a_hub_element(suite: OnetSuite) -> None:
    # 120 elements carry every occupation's edges, so a hub's strongest
    # neighbours are almost never the occupation being asked about. A hop that
    # lands on the target is exempt from the branching cap for that reason.
    result = suite.enumerate_paths(SOFTWARE_DEVELOPERS, DATA_SCIENTISTS, max_depth=2, max_paths=10)

    assert result.paths
    assert result.pruning is not None
    assert result.pruning.returned == len(result.paths)
    for path in result.paths:
        assert path.node_ids[0] == SOFTWARE_DEVELOPERS
        assert path.node_ids[-1] == DATA_SCIENTISTS


def test_pathfind_reports_a_missing_endpoint(suite: OnetSuite) -> None:
    result = suite.enumerate_paths(SOFTWARE_DEVELOPERS, "onet:occupation:99-9999.00")

    assert result.warnings == ["endpoint_not_found"]


def test_score_paths_ranks_real_paths_under_each_declared_policy(suite: OnetSuite) -> None:
    # This is the capability ESCO cannot have: the ranking comes from published
    # Importance ratings, not from a modelling decision dressed as data.
    found = suite.enumerate_paths(PROGRAMMING, "onet:element:2.C.3.a", max_depth=2, max_paths=20)
    assert len(found.paths) > 1

    for policy in (POLICY_BOTTLENECK, POLICY_MEAN, POLICY_LOWER_CI):
        result = suite.score_paths(found.paths, policy)
        scores = [s.score for s in result.scored_paths]

        assert len(scores) == len(found.paths)
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 <= s <= 1.0 for s in scores)
        assert all(s.policy == policy for s in result.scored_paths)
        assert result.meta["policy"] == policy.model_dump()


def test_score_paths_refuses_a_policy_it_does_not_define(suite: OnetSuite) -> None:
    found = suite.enumerate_paths(PROGRAMMING, "onet:element:2.C.3.a", max_depth=2, max_paths=5)

    result = suite.score_paths(found.paths, PolicyRef(name="whatever-feels-right", version="1"))

    assert result.scored_paths == []
    assert result.warnings[0].startswith("unknown_policy:")
