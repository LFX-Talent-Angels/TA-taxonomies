"""Load + validate the SFIA fixture in Neo4j (skipped if the DB is down).

The tests that matter most here are the ones that run against a graph which
already contains something else. A load against an empty graph proves the loader
*writes*; it does not prove the loader *coexists*, and the second is the claim
that ships.
"""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.sfia.config import LEVELS
from ta_taxonomies.suites.sfia.db import neo4j_driver
from ta_taxonomies.suites.sfia.load import (
    SfiaLoadValidationError,
    load_normalized,
    normalize_document,
    run_load,
    validate_load,
)

pytestmark = pytest.mark.neo4j


@pytest.fixture(scope="module")
def loaded() -> dict[str, int]:
    return run_load(mode="fixture", wipe=True)


def test_fixture_load_validates(loaded: dict[str, int]) -> None:
    assert loaded["skills"] == 14
    assert loaded["levels"] == len(LEVELS)
    assert loaded["may_lead_to"] == len(LEVELS) - 1
    assert loaded["has_level"] == 67
    assert loaded["related_to"] == 50
    # The public SFIA pages carry no category view, so the fixture has no
    # groups. Asserted rather than left implicit: a future fixture that gains
    # them should have to change this line on purpose.
    assert loaded["categories"] == 0
    assert loaded["broader_than"] == 0


def test_every_loaded_skill_reaches_at_least_one_level(loaded: dict[str, int]) -> None:
    """The invariant that is true of SFIA and false of O*NET.

    122 of O*NET's 1,016 occupations carry no ratings, so the equivalent
    assertion there would refuse correct data. A SFIA skill defined at no level
    does not exist in the framework.
    """
    del loaded
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            orphans = session.run(
                "MATCH (s:SfiaSkill {source: 'sfia'}) "
                "WHERE NOT (s)-[:HAS_LEVEL]->(:SfiaLevel) RETURN count(s) AS c"
            ).single()
    assert orphans is not None and orphans["c"] == 0


def test_no_edge_carries_an_invented_essential_flag(loaded: dict[str, int]) -> None:
    """An assertion that a value is *absent*, which is unusual and deliberate.

    The tempting shortcut is to stamp ``relation_type`` so ESCO-shaped callers
    keep working. SFIA publishes no essential/optional distinction, so doing so
    would present our modelling as source data.
    """
    del loaded
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            flagged = session.run(
                "MATCH (:SfiaNode)-[r]->(:SfiaNode) "
                "WHERE r.relation_type IS NOT NULL RETURN count(r) AS c"
            ).single()
    assert flagged is not None and flagged["c"] == 0


def test_the_graph_holds_no_string_long_enough_to_be_sfia_prose(
    loaded: dict[str, int],
) -> None:
    del loaded
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            longest = session.run(
                "MATCH (n:SfiaNode {source: 'sfia'}) UNWIND keys(n) AS k "
                "WITH n, k WHERE n[k] IS :: STRING "
                "RETURN max(size(n[k])) AS longest"
            ).single()
    assert longest is not None
    assert longest["longest"] <= 120


def test_load_does_not_touch_another_suite_sharing_the_canonical_labels(
    loaded: dict[str, int],
) -> None:
    # :Skill and :Level are the shared vocabulary, so a wipe scoped by canonical
    # label would delete another suite's graph. Prove it does not.
    del loaded
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            session.run(
                "MERGE (n:EscoNode:Skill {id: 'esco:skill:sentinel'}) "
                "SET n.source = 'esco', n.source_id = 'sentinel'"
            )

    run_load(mode="fixture", wipe=True)

    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            survivor = session.run(
                "MATCH (n:Skill {id: 'esco:skill:sentinel'}) RETURN n.source AS source"
            ).single()
            session.run("MATCH (n {id: 'esco:skill:sentinel'}) DETACH DELETE n")

    assert survivor is not None
    assert survivor["source"] == "esco"


def test_a_node_holding_a_sfia_id_without_the_umbrella_label_is_named(
    loaded: dict[str, int],
) -> None:
    """The duplicate that validates clean unless someone looks for it.

    A crosswalk that materialises an endpoint before this suite is loaded leaves
    a ``(:Skill {id:'sfia:skill:…'})`` with none of this suite's labels. MERGE on
    the umbrella cannot see it — MERGE matches the whole pattern, labels
    included — so a second node is created, the uniqueness constraint cannot see
    the pair either (constraints are per label), and every count still adds up.
    """
    del loaded
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            session.run("MATCH (n:SfiaNode) DETACH DELETE n")
            session.run(
                "MERGE (p:Skill {id: 'sfia:skill:PROG'}) SET p.source = 'crosswalk-placeholder'"
            )

    with pytest.raises(SfiaLoadValidationError, match="without the :SfiaNode label"):
        run_load(mode="fixture", wipe=True)

    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            twins = session.run("MATCH (n {id: 'sfia:skill:PROG'}) RETURN count(n) AS c").single()
            session.run("MATCH (n {id: 'sfia:skill:PROG'}) WHERE NOT n:SfiaNode DETACH DELETE n")

    # The load did write its own node; the point is that the pair was detected
    # and named rather than merged around or silently tolerated.
    assert twins is not None and twins["c"] == 2

    run_load(mode="fixture", wipe=True)


def test_validation_fails_on_a_count_that_does_not_survive(loaded: dict[str, int]) -> None:
    with neo4j_driver() as (driver, database):
        with pytest.raises(SfiaLoadValidationError, match="count mismatch"):
            validate_load(driver, {**loaded, "skills": loaded["skills"] + 1}, database=database)


def test_the_category_tree_loads_when_a_source_carries_one() -> None:
    """Exercised with synthetic groups, because SFIA's real ones are login-gated.

    Without this the whole BROADER_THAN path would be unreachable in CI, since
    the committed fixture legitimately has no categories.
    """
    document = {
        "skills": [
            {
                "code": "WIDG",
                "name": "SYNTHETIC skill",
                "slug": "synthetic-skill",
                "levels": [3, 4],
                "category": "SYNTHETIC CATEGORY",
                "subcategory": "SYNTHETIC SUBCATEGORY",
                "related_slugs": [],
            }
        ],
        "codes_by_slug": {"synthetic-skill": "WIDG"},
    }
    payload = normalize_document(document)
    with neo4j_driver() as (driver, database):
        counts = load_normalized(driver, payload, database=database, wipe=True)
        expected = {key: counts[key] for key in ("levels", "skills", "categories", "subcategories")}
        expected["broader_than"] = len(payload["broader_than"])
        live = validate_load(driver, expected, database=database)

    assert live["categories"] == 1
    assert live["subcategories"] == 1
    assert live["broader_than"] == 2

    run_load(mode="fixture", wipe=True)


def test_an_edge_stamped_with_an_essential_flag_stops_validation(
    loaded: dict[str, int],
) -> None:
    """The absence assertion, made load-bearing.

    Checking the graph for ``relation_type`` passes whether or not
    ``validate_load`` looks for it, because nothing writes one today. Removing
    the assertion therefore left every test green — verified by mutation. This
    seeds the value the assertion exists to catch, so the guard is what fails.
    """
    del loaded
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            session.run(
                "MATCH (:SfiaSkill {id: 'sfia:skill:PROG'})-[r:HAS_LEVEL]->(:SfiaLevel) "
                "WITH r LIMIT 1 SET r.relation_type = 'essential'"
            )
        with pytest.raises(SfiaLoadValidationError, match="relation_type"):
            validate_load(driver, {}, database=database)

    run_load(mode="fixture", wipe=True)
