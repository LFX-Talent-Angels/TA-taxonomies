"""Load + validate the O*NET fixture in Neo4j (skipped if the DB is down)."""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.onet.db import neo4j_driver
from ta_taxonomies.suites.onet.load import OnetLoadValidationError, run_load, validate_load

pytestmark = pytest.mark.neo4j


@pytest.fixture(scope="module")
def loaded() -> dict[str, int]:
    return run_load(mode="fixture", wipe=True)


def test_fixture_load_validates(loaded: dict[str, int]) -> None:
    assert loaded["occupations"] == 4
    assert loaded["soc_groups"] == 4
    # All four Content Model branches this suite models: 1.A abilities,
    # 2.A basic skills, 2.B cross-functional skills, 2.C knowledge.
    assert loaded["elements"] == 120
    # Not every occupation rates every element, so this is below the 480 a
    # dense slice would give — the exact number is a property of the committed
    # fixture and changes only when it is regenerated.
    assert loaded["has_skill"] == 360
    assert loaded["has_skill"] < loaded["occupations"] * loaded["elements"]
    assert loaded["classified_under"] == loaded["occupations"]
    assert loaded["tasks"] > 0
    assert loaded["broader_than"] > 0


def test_load_does_not_touch_another_suite_sharing_the_canonical_labels(
    loaded: dict[str, int],
) -> None:
    # :Occupation and :Skill are the shared vocabulary, so a wipe scoped by
    # canonical label would delete ESCO's graph. Prove it does not.
    del loaded
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            session.run(
                "MERGE (n:Occupation {id: 'esco:occupation:sentinel'}) "
                "SET n.source = 'esco', n.source_id = 'sentinel'"
            )

    run_load(mode="fixture", wipe=True)

    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            survivor = session.run(
                "MATCH (n:Occupation {id: 'esco:occupation:sentinel'}) RETURN n.source AS source"
            ).single()
            session.run("MATCH (n {id: 'esco:occupation:sentinel'}) DETACH DELETE n")

    assert survivor is not None
    assert survivor["source"] == "esco"


def test_validation_fails_on_a_count_that_does_not_survive(loaded: dict[str, int]) -> None:
    # The validate stage is the load's own assertion, so prove it can fail
    # rather than trusting that it passed.
    with neo4j_driver() as (driver, database):
        with pytest.raises(OnetLoadValidationError, match="occupations count mismatch"):
            validate_load(
                driver,
                {"occupations": loaded["occupations"] + 1},
                database=database,
            )


def test_every_rated_edge_keeps_its_statistics(loaded: dict[str, int]) -> None:
    del loaded
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            record = session.run(
                """
                MATCH (:OnetOccupation)-[r:HAS_SKILL]->(:OnetElement)
                RETURN count(r) AS total,
                       count(r.importance) AS importance,
                       count(r.level) AS level,
                       count(r.importance_n) AS sample_size,
                       count(r.relation_type_policy) AS policy
                """
            ).single()

    assert record is not None
    # Importance, Level and the sample size are what make score_paths
    # implementable here and a stub in ESCO; losing any of them silently would
    # turn a real ranking into a plausible one.
    assert record["importance"] == record["total"]
    assert record["level"] == record["total"]
    assert record["sample_size"] == record["total"]
    assert record["policy"] == record["total"]
