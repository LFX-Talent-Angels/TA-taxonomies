"""search_nodes / get_neighbors / enumerate_paths against the BLS fixture in Neo4j.

Skipped when Neo4j is not reachable. Loads into whatever NEO4J_* names and
wipes BLS nodes there first.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.contract.models import PolicyRef
from ta_taxonomies.suites.bls.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.bls.load import run_load
from ta_taxonomies.suites.bls.tools import BlsSuite


def _neo4j_available() -> bool:
    if not os.getenv("NEO4J_PASSWORD"):
        return False
    try:
        with neo4j_driver() as (driver, _db):
            verify_connectivity(driver)
        return True
    except (ServiceUnavailable, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _neo4j_available(),
    reason="Neo4j not available (start with: docker compose up -d)",
)


@pytest.fixture(autouse=True)
def _no_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the meaning tier off: these tests must not load an ML model."""
    monkeypatch.setattr("ta_taxonomies.suites.bls.tools._embed_query", lambda _q: None)


@pytest.fixture(scope="module")
def suite() -> Iterator[BlsSuite]:
    from neo4j import GraphDatabase

    from ta_taxonomies.suites.bls.db import neo4j_config_from_env

    run_load(mode="fixture", wipe=True)
    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    yield BlsSuite(driver, database=cfg["database"])
    driver.close()


def test_exact_title_carries_the_bls_facts(suite: BlsSuite) -> None:
    result = suite.search_nodes("Software Developers", kind="occupation")
    top = result.candidates[0]
    assert (top.method, top.node.id) == ("exact_pref", "bls:occupation:15-1252")
    props = top.node.properties
    assert props["soc_code"] == "15-1252"
    assert props["median_wage_annual"] > 0
    assert props["entry_education"] == "Bachelor's degree"
    assert props["projection_cycle"] == "2025-2035"
    assert props["onet_soc_codes"] == ["15-1252.00"]


def test_direct_match_titles_find_the_occupation(suite: BlsSuite) -> None:
    # BLS's direct-match titles are the aliases: everyday names for a SOC code.
    result = suite.search_nodes("Software Engineer", kind="occupation")
    assert [(c.method, c.node.id) for c in result.candidates][:1] == [
        ("exact_alt", "bls:occupation:15-1252")
    ]
    nurse = suite.search_nodes("Certified Nursing Assistant", kind="occupation")
    assert nurse.candidates[0].node.id == "bls:occupation:31-1131"


def test_codes(suite: BlsSuite) -> None:
    for code in ("15-1252", "15-1252.00"):
        result = suite.search_nodes(code)
        assert [(c.method, c.node.id) for c in result.candidates] == [
            ("exact_code", "bls:occupation:15-1252")
        ]
    group = suite.search_nodes("15-1250")
    assert [c.node.id for c in group.candidates] == ["bls:soc-group:15-1250"]


def test_skill_search(suite: BlsSuite) -> None:
    result = suite.search_nodes("Mathematics", kind="skill")
    assert result.candidates[0].node.id == "bls:skill:mathematics"


def test_unknown_kind_is_reported(suite: BlsSuite) -> None:
    # The tagging router drops kinds a suite does not have.
    assert suite.search_nodes("bake bread", kind="task").warnings == ["unknown_kind:task"]


def test_neighbours_list_skills_by_score(suite: BlsSuite) -> None:
    result = suite.get_neighbors("bls:occupation:15-1252", ["HAS_SKILL"])
    scores = [e.properties["score"] for e in result.edges]
    assert scores == sorted(scores, reverse=True)
    assert result.edges[0].to_id == "bls:skill:computers-and-information-technology"
    assert {e.properties["relation_type"] for e in result.edges} <= {"essential", "optional"}


def test_neighbours_include_education_and_group(suite: BlsSuite) -> None:
    result = suite.get_neighbors("bls:occupation:29-1161")
    types = {e.type for e in result.edges}
    assert {"HAS_SKILL", "CLASSIFIED_UNDER", "REQUIRES_EDUCATION"} <= types
    labels = {n.id: n.label for n in result.nodes}
    assert labels["bls:education:master-s-degree"] == "Master's degree"


def test_neighbours_refuse_unknown_relationships(suite: BlsSuite) -> None:
    warnings = suite.get_neighbors("bls:occupation:15-1252", ["DROP_ALL"]).warnings
    assert warnings == ["unknown_rel_types:['DROP_ALL']"]
    assert suite.get_neighbors("bls:occupation:99-9999").warnings == ["node_not_found"]


def test_paths_between_two_jobs(suite: BlsSuite) -> None:
    result = suite.enumerate_paths(
        "bls:occupation:15-1252", "bls:occupation:15-1254", max_depth=2, max_paths=5
    )
    assert result.paths
    assert result.pruning is not None and result.pruning.returned == len(result.paths)
    for path in result.paths:
        assert path.node_ids[0] == "bls:occupation:15-1252"
        assert path.node_ids[-1] == "bls:occupation:15-1254"
        assert len(set(path.node_ids)) == len(path.node_ids)


def test_path_guards(suite: BlsSuite) -> None:
    assert suite.enumerate_paths("a", "b", max_depth=0).warnings == ["invalid_max_depth"]
    assert suite.enumerate_paths("a", "b", max_paths=0).warnings == ["invalid_max_paths"]
    missing = suite.enumerate_paths("bls:occupation:15-1252", "bls:occupation:99-9999")
    assert missing.warnings == ["endpoint_not_found"]


def test_score_paths_is_a_declared_stub(suite: BlsSuite) -> None:
    result = suite.score_paths([], PolicyRef(name="anything", version="1"))
    assert result.warnings[0] == "score_paths_not_implemented"
    assert result.scored_paths == []


def test_the_branching_cap_never_cuts_the_edge_to_the_target(
    suite: BlsSuite, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Live: a BLS skill links ~800 occupations, so a cap of 25 cut the target
    # off at the shared skill ("no_path" for jobs sharing 12 skills).
    monkeypatch.setattr("ta_taxonomies.suites.bls.tools.MAX_BRANCHING", 1)
    result = suite.enumerate_paths(
        "bls:occupation:15-1252", "bls:occupation:29-1141", max_depth=2, max_paths=5
    )
    assert result.paths, result.warnings
    assert all(p.node_ids[-1] == "bls:occupation:29-1141" for p in result.paths)


def test_a_code_is_found_whatever_kind_is_asked(suite: BlsSuite) -> None:
    # The chat planner asks for an occupation; "15-1250" is a broad group.
    result = suite.search_nodes("SOC 15-1250", kind="occupation")
    assert [(c.method, c.node.id) for c in result.candidates] == [
        ("exact_code", "bls:soc-group:15-1250")
    ]
