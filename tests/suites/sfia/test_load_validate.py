"""Load + validate SFIA fixture in Neo4j (skipped if DB down)."""

from __future__ import annotations

import os

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.sfia.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.sfia.load import run_load


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
            # A foreign Skill (shared canonical label) must survive the SFIA wipe.
            session.run(
                "MERGE (n:Skill {id: 'esco:test-skill-sfia-wipe'}) "
                "SET n.source = 'esco', n.source_id = 'test-skill-sfia-wipe'"
            )

    counts = run_load(mode="fixture", wipe=True)

    assert counts == {
        "categories": 2,
        "subcategories": 2,
        "skills": 5,
        "skill_levels": 22,
        "has_level": 22,
        "broader_than": 7,
    }

    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            survivor = session.run(
                "MATCH (n:Skill {id: 'esco:test-skill-sfia-wipe'}) RETURN n.source AS source"
            ).single()
            session.run("MATCH (n {id: 'esco:test-skill-sfia-wipe'}) DETACH DELETE n")
    assert survivor is not None
    assert survivor["source"] == "esco"


def test_reload_is_idempotent() -> None:
    first = run_load(mode="fixture", wipe=False)
    second = run_load(mode="fixture", wipe=False)
    assert first == second
