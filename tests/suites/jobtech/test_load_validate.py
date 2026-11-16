"""Load + validate the JobTech fixture in Neo4j (skipped if the database is down).

Loads into whatever NEO4J_* names and wipes JobTech nodes there first; other
suites' nodes are left alone, which this test checks.
"""

from __future__ import annotations

import os

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.jobtech.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.jobtech.load import run_load


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
            session.run(
                "MERGE (n:Skill {id: 'esco:test-skill-jobtech-wipe'}) "
                "SET n.source = 'esco', n.source_id = 'test-skill-jobtech-wipe'"
            )

    counts = run_load(mode="fixture", wipe=True)
    assert counts["occupations"] == 11
    assert counts["classified_under"] == 11
    assert counts["substitutes"] == 2

    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            survivor = session.run(
                "MATCH (n:Skill {id: 'esco:test-skill-jobtech-wipe'}) RETURN n.source AS s"
            ).single()
            session.run("MATCH (n {id: 'esco:test-skill-jobtech-wipe'}) DETACH DELETE n")
            baker = session.run(
                "MATCH (o:Occupation:JobtechNode {id: 'jobtech:occupation:XugJ_6hw_wVV'}) "
                "RETURN o.esco_exact AS esco, o.isco_codes AS isco"
            ).single()
    assert survivor is not None and survivor["s"] == "esco"
    assert baker is not None and baker["esco"] and baker["isco"] == ["7512"]


def test_reload_is_idempotent() -> None:
    first = run_load(mode="fixture", wipe=True)
    assert run_load(mode="fixture", wipe=False) == first
