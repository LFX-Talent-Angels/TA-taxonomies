"""search_nodes contract tests against the ESCO fixture in Neo4j.

Skipped when Neo4j is not reachable (CI without Docker).
"""

from __future__ import annotations

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.esco.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.esco.load import run_load
from ta_taxonomies.suites.esco.tools import EscoSuite


def _neo4j_available() -> bool:
    import os

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


@pytest.fixture(scope="module")
def loaded_suite() -> EscoSuite:
    """Load fixture once; provide suite with a live driver for the module."""
    from neo4j import GraphDatabase

    from ta_taxonomies.suites.esco.db import neo4j_config_from_env

    run_load(mode="fixture", wipe=True)
    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    suite = EscoSuite(driver, database=cfg["database"])
    yield suite
    driver.close()


def test_search_exact_pref_software_developer(loaded_suite: EscoSuite) -> None:
    result = loaded_suite.search_nodes("software developer", kind="occupation")
    assert not any(w == "not_found" for w in result.warnings)
    assert result.candidates
    top = result.candidates[0]
    assert top.method == "exact_pref"
    assert top.confidence >= 0.95
    assert top.node.label == "software developer"
    assert top.node.id.startswith("esco:occupation:")
    assert top.node.source == "esco"
    assert top.node.source_id.startswith("http://")


def test_search_casefold(loaded_suite: EscoSuite) -> None:
    result = loaded_suite.search_nodes("Software Developer", kind="occupation")
    assert result.candidates
    assert result.candidates[0].node.label.lower() == "software developer"


def test_search_not_found(loaded_suite: EscoSuite) -> None:
    result = loaded_suite.search_nodes("zzznofuchoccupation999", kind="occupation")
    assert result.candidates == []
    assert "not_found" in result.warnings


def test_search_contains_partial(loaded_suite: EscoSuite) -> None:
    result = loaded_suite.search_nodes("data scien", kind="occupation")
    assert result.candidates
    labels = {c.node.label.lower() for c in result.candidates}
    assert any("data scientist" in lab for lab in labels)


def test_search_skill_kind(loaded_suite: EscoSuite) -> None:
    # pick any skill label from graph via a broad contains that should hit
    result = loaded_suite.search_nodes("python", kind="skill")
    # fixture may not include python — allow not_found but never invent
    if result.candidates:
        assert all(c.node.id.startswith("esco:skill:") for c in result.candidates)
    else:
        assert "not_found" in result.warnings


def test_search_unknown_kind_returns_warning(loaded_suite: EscoSuite) -> None:
    result = loaded_suite.search_nodes("software developer", kind="fruit")

    assert result.candidates == []
    assert result.warnings == ["unknown_kind:fruit"]


def test_contains_keeps_title_matches_ahead_of_alias_only_matches(
    loaded_suite: EscoSuite,
) -> None:
    """Live repro: short titles that matched only through an alias filled the
    25-row cut, so "engineer" lost most "... engineer" titles."""
    noise = [
        {"id": f"esco:occupation:zorbler-{i:02d}", "pref": f"zj{i:02d}", "alt": f"zorbler {i}"}
        for i in range(30)
    ]
    title = {"id": "esco:occupation:zorbler-title", "pref": "senior zorbler technician", "alt": ""}
    with loaded_suite._session() as session:
        session.run(
            """
            UNWIND $rows AS r
            CREATE (n:EscoNode:Occupation {id: r.id, pref_label: r.pref, source: 'esco',
                    source_id: r.id, kind: 'Occupation',
                    alt_labels: CASE r.alt WHEN '' THEN [] ELSE [r.alt] END})
            """,
            rows=[*noise, title],
        )
        session.run("CALL db.index.fulltext.awaitEventuallyConsistentIndexRefresh()")
    try:
        result = loaded_suite.search_nodes("zorbler", kind="occupation")
        labels = [candidate.node.label for candidate in result.candidates]
        assert labels[0] == "senior zorbler technician"
        assert "truncated" in result.warnings
    finally:
        with loaded_suite._session() as session:
            session.run(
                "MATCH (n:EscoNode) WHERE n.id STARTS WITH $prefix DETACH DELETE n",
                prefix="esco:occupation:zorbler-",
            )
