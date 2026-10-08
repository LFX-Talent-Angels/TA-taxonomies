"""Live O*NET Locate/Connect checks. Do not wipe the graph.

Skipped when Neo4j is down or O*NET has not been loaded. These tests never
call ``run_load(mode='fixture')`` so a full 31.0 graph stays intact.
"""

from __future__ import annotations

import os

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.onet.db import neo4j_config_from_env, neo4j_driver, verify_connectivity
from ta_taxonomies.suites.onet.tools import OnetSuite


def _neo4j_available() -> bool:
    if not os.getenv("NEO4J_PASSWORD"):
        return False
    try:
        with neo4j_driver() as (driver, _db):
            verify_connectivity(driver)
        return True
    except (ServiceUnavailable, OSError):
        return False


def _onet_loaded() -> bool:
    if not _neo4j_available():
        return False
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            rec = session.run(
                "MATCH (n:OnetNode {id: 'onet:occupation:15-1252.00'}) RETURN n.id AS id"
            ).single()
    return rec is not None


pytestmark = [
    pytest.mark.skipif(not _neo4j_available(), reason="Neo4j not available"),
    pytest.mark.skipif(not _onet_loaded(), reason="O*NET graph is not loaded"),
]


@pytest.fixture
def suite() -> OnetSuite:
    from neo4j import GraphDatabase

    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    yield OnetSuite(driver, database=cfg["database"])
    driver.close()


def test_search_exact_pref_software_developers(suite: OnetSuite) -> None:
    result = suite.search_nodes("Software Developers", kind="occupation")
    assert result.candidates
    top = result.candidates[0]
    assert top.method == "exact_pref"
    assert top.node.id == "onet:occupation:15-1252.00"
    assert top.node.source == "onet"
    assert top.node.label == "Software Developers"


def test_search_job_title_alias_software_engineer(suite: OnetSuite) -> None:
    result = suite.search_nodes("Software Engineer", kind="occupation")
    assert result.candidates
    ids = [c.node.id for c in result.candidates]
    assert "onet:occupation:15-1252.00" in ids
    assert result.candidates[0].method in {"exact_alt", "exact_pref", "contains"}


def test_search_unknown_kind(suite: OnetSuite) -> None:
    result = suite.search_nodes("developer", kind="spaceship")
    assert result.warnings == ["unknown_kind:spaceship"]


def test_search_does_not_return_esco_ids(suite: OnetSuite) -> None:
    result = suite.search_nodes("Software Developers", kind="occupation")
    assert result.candidates
    assert all(node.id.startswith("onet:") for node in result.nodes)


def test_neighbors_include_weighted_skills(suite: OnetSuite) -> None:
    result = suite.get_neighbors(
        "onet:occupation:15-1252.00",
        rel_types=["HAS_SKILL"],
    )
    assert result.edges
    assert all(edge.type == "HAS_SKILL" for edge in result.edges)
    programming = [
        edge
        for edge in result.edges
        if edge.to_id == "onet:element:2.B.3.e" or edge.from_id == "onet:element:2.B.3.e"
    ]
    assert programming
    assert programming[0].properties.get("importance") == 4.0
    assert programming[0].properties.get("relation_type") == "transferable"


def test_neighbors_software_filter(suite: OnetSuite) -> None:
    result = suite.get_neighbors(
        "onet:occupation:15-1252.00",
        rel_types=["USES_SOFTWARE"],
    )
    assert result.edges
    assert all(edge.type == "USES_SOFTWARE" for edge in result.edges)
    labels = {node.label for node in result.nodes}
    assert "Python" in labels or any("python" in lab.lower() for lab in labels)
