"""Load + validate the BLS fixture in Neo4j (skipped if the database is down).

Loads into whatever NEO4J_* names and wipes BLS nodes there first; other
suites' nodes are left alone, which this test checks.
"""

from __future__ import annotations

import os

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.bls.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.bls.load import run_load


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


def test_fixture_load_validates() -> None:
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            # A foreign Occupation (shared canonical label) must survive the BLS wipe.
            session.run(
                "MERGE (n:Occupation {id: 'onet:test-occupation-bls-wipe'}) "
                "SET n.source = 'onet', n.source_id = 'test-occupation-bls-wipe'"
            )

    counts = run_load(mode="fixture", wipe=True)
    assert counts["occupations"] == 15
    assert counts["groups"] == 23
    assert counts["skills"] == 17
    assert counts["classified_under"] == 15
    assert counts["has_skill"] > 100

    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            survivor = session.run(
                "MATCH (n:Occupation {id: 'onet:test-occupation-bls-wipe'}) "
                "RETURN n.source AS source"
            ).single()
            session.run("MATCH (n {id: 'onet:test-occupation-bls-wipe'}) DETACH DELETE n")
            dev = session.run(
                "MATCH (o:Occupation:BlsNode {id: 'bls:occupation:15-1252'}) "
                "RETURN o.median_wage_annual AS wage, o.alt_labels AS alts"
            ).single()
    assert survivor is not None and survivor["source"] == "onet"
    assert dev is not None and dev["wage"] > 0 and dev["alts"]


def test_reload_is_idempotent() -> None:
    first = run_load(mode="fixture", wipe=True)
    second = run_load(mode="fixture", wipe=False)
    assert first == second
