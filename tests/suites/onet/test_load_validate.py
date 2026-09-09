"""Load + validate O*NET fixture in Neo4j (skipped if DB down)."""

from __future__ import annotations

import os

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.onet.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.onet.load import run_load


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


def test_fixture_load_validates_and_does_not_wipe_esco() -> None:
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            session.run(
                "MERGE (n:Occupation {id: 'esco:test-occupation'}) "
                "SET n:EscoNode, n.source = 'esco', n.source_id = 'test-occupation'"
            )

    counts = run_load(mode="fixture", wipe=True)

    assert counts["occupations"] == 4
    assert counts["has_skill"] >= 1
    assert counts["software"] >= 1

    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            survivor = session.run(
                "MATCH (n:Occupation {id: 'esco:test-occupation'}) RETURN n.source AS source"
            ).single()
            session.run("MATCH (n {id: 'esco:test-occupation'}) DETACH DELETE n")
            software_dev = session.run(
                "MATCH (n:OnetNode {id: 'onet:occupation:15-1252.00'}) "
                "RETURN n.pref_label AS label, n.source AS source"
            ).single()
    assert survivor is not None
    assert survivor["source"] == "esco"
    assert software_dev is not None
    assert software_dev["label"] == "Software Developers"
    assert software_dev["source"] == "onet"
