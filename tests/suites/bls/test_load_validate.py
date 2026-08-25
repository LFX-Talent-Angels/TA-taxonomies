"""Load and validation against a live Neo4j (skipped if the DB is down).

The tests that earn their place here are the **coexistence** ones. From
``suites/onet/NOTES.md``:

    A load against an empty graph proves the loader *writes*. It does not prove
    the loader *coexists*. Those are different claims, and the one that ships is
    the second.

Every failure in that class needs a pre-existing node to express itself, so an
empty-graph test cannot reach them by construction. These plant one first.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from ta_taxonomies.suites.bls.config import (
    LABEL_BLS_NODE,
    LABEL_INDUSTRY,
    LABEL_OCCUPATION,
    LABEL_SOC_GROUP,
    REL_BROADER_THAN,
    REL_EMPLOYED_IN,
    SOC_LEVEL_MAJOR,
    SOC_LEVEL_MINOR,
)
from ta_taxonomies.suites.bls.db import neo4j_driver
from ta_taxonomies.suites.bls.load import BlsLoadValidationError, run_load, validate_load

pytestmark = pytest.mark.neo4j

SOFTWARE_DEVELOPERS = "bls:occupation:15-1252"


@pytest.fixture(scope="module")
def loaded() -> Iterator[dict[str, int]]:
    yield run_load(mode="fixture", wipe=True)


def _scalar(cypher: str, **params: Any) -> Any:
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            record = session.run(cypher, **params).single()
            return None if record is None else record[0]


def test_the_fixture_loads_and_validates(loaded: dict[str, int]) -> None:
    assert loaded["occupations"] > 0
    assert loaded["industries"] > 0
    assert loaded["broader_than"] > 0
    assert loaded["employed_in"] > 0


def test_every_node_carries_three_labels_and_industries_carry_two(
    loaded: dict[str, int],
) -> None:
    """The umbrella keys MERGE, the concrete label is what search seeks, the
    canonical one is for cross-suite reads — and industries have no canonical
    kind to take, which is a contract gap rather than an oversight."""
    assert (
        _scalar(
            f"MATCH (n:{LABEL_OCCUPATION}) WHERE NOT (n:{LABEL_BLS_NODE} AND n:Occupation) "
            "RETURN count(n)"
        )
        == 0
    )
    assert (
        _scalar(
            f"MATCH (n:{LABEL_SOC_GROUP}) WHERE NOT (n:{LABEL_BLS_NODE} AND n:SOCGroup) "
            "RETURN count(n)"
        )
        == 0
    )
    assert _scalar(
        f"MATCH (n:{LABEL_INDUSTRY}) RETURN count(n) = count(CASE WHEN n:{LABEL_BLS_NODE} "
        "THEN 1 END)"
    )


def test_the_spine_is_rooted_and_no_edge_skips_a_level(loaded: dict[str, int]) -> None:
    """Every node reaches a major group, and every hop is exactly one level.

    The second is only true because the derived-group pass runs; a skip means
    it dropped a group, and nothing else in the load would notice — the edge
    would simply land higher up and every count would still add up.
    """
    assert (
        _scalar(
            f"MATCH (n:{LABEL_BLS_NODE}) WHERE n.soc_level IS NOT NULL "
            f"AND n.soc_level <> '{SOC_LEVEL_MAJOR}' "
            f"AND NOT (n)-[:{REL_BROADER_THAN}]->() RETURN count(n)"
        )
        == 0
    )
    assert (
        _scalar(
            f"MATCH ()-[r:{REL_BROADER_THAN}]->() WHERE coalesce(r.levels_skipped, 0) > 0 "
            "RETURN count(r)"
        )
        == 0
    )


def test_the_suite_has_no_skills_layer_and_the_graph_says_so(
    loaded: dict[str, int],
) -> None:
    """ADR-0006 adopts BLS for this. An invariant nobody enforces is a comment."""
    assert _scalar(f"MATCH (:{LABEL_BLS_NODE})-[r:HAS_SKILL]->() RETURN count(r)") == 0


def test_employment_edges_end_on_an_industry(loaded: dict[str, int]) -> None:
    assert (
        _scalar(
            f"MATCH (:{LABEL_BLS_NODE})-[r:{REL_EMPLOYED_IN}]->(x) "
            f"WHERE NOT x:{LABEL_INDUSTRY} RETURN count(r)"
        )
        == 0
    )


def test_the_units_are_on_the_node_because_1693_8_means_nothing_alone(
    loaded: dict[str, int],
) -> None:
    record = _scalar(
        f"MATCH (n:{LABEL_OCCUPATION} {{id: $id}}) "
        "RETURN [n.employment_unit, n.wage_unit, n.base_year, n.projection_year]",
        id=SOFTWARE_DEVELOPERS,
    )
    assert record == ["thousands of jobs", "USD per year", 2024, 2034]


# --- coexistence -----------------------------------------------------------


def test_a_foreign_node_holding_one_of_our_ids_stops_the_load(
    loaded: dict[str, int],
) -> None:
    """The failure that only exists in a shared graph.

    A crosswalk that materialises an endpoint before its suite is loaded leaves
    a node with the right id and none of this suite's labels. MERGE keys on the
    umbrella label, so it does not match — it creates a second node. The
    uniqueness constraint cannot see the collision, because constraints are per
    label; every count still adds up, because the count queries are label-scoped
    too. Two nodes share one id and nothing says so unless this check exists.

    The wipe does not remove it either: deleting another package's node is not
    this loader's call. Failing and naming it is.
    """
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            session.run(
                "CREATE (:CrosswalkStub {id: $id, note: 'placeholder'})", id=SOFTWARE_DEVELOPERS
            )
    try:
        with pytest.raises(BlsLoadValidationError, match="without the :BlsNode label"):
            run_load(mode="fixture", wipe=True)

        # And the duplicate really is there — the check is not firing on a
        # node that MERGE quietly absorbed.
        assert _scalar("MATCH (n) WHERE n.id = $id RETURN count(n)", id=SOFTWARE_DEVELOPERS) == 2
    finally:
        with neo4j_driver() as (driver, database):
            with driver.session(database=database) as session:
                session.run("MATCH (n:CrosswalkStub) DETACH DELETE n")
        run_load(mode="fixture", wipe=True)


def test_an_invented_minor_group_stops_the_load_at_validation(
    loaded: dict[str, int],
) -> None:
    """The post-load half of the minor-group finding.

    ``normalize_document`` refuses a derived minor before anything is written,
    but that guard is upstream of the database and this file's whole premise is
    that post-load validation has holes by construction. Measured by mutation:
    with the derivation bug restored and the normalize guard removed, the load
    succeeded, validated clean, and produced 590 SOC groups instead of 575.

    The invariant is exact. Reconstruction only ever produces a broad or a major
    group — those two levels are derivable from the code — while the minor level
    is looked up and ``resolve_soc_minor`` returns only published codes. So a
    minor group carrying ``title_source='derived'`` is an invented SOC code, and
    there is no legitimate way for one to exist.
    """
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            # Rooted deliberately: the "unrooted node" check fires earlier in
            # validate_load and would mask the one under test, leaving the test
            # green for the wrong reason.
            session.run(
                f"""
                MATCH (parent:{LABEL_BLS_NODE} {{id: 'bls:soc:29-0000'}})
                CREATE (n:{LABEL_BLS_NODE}:{LABEL_SOC_GROUP} {{
                    id: 'bls:soc:29-1100', source: $source, source_id: '29-1100',
                    code: '29-1100', pref_label: '', alt_labels: [],
                    soc_level: '{SOC_LEVEL_MINOR}', title_source: 'derived'
                }})
                CREATE (n)-[:{REL_BROADER_THAN} {{levels_skipped: 0}}]->(parent)
                """,
                source="bls",
            )
    try:
        with neo4j_driver() as (driver, database):
            # An empty `expected` skips the count comparison and leaves only the
            # invariants, which is the part under test here.
            with pytest.raises(BlsLoadValidationError, match="title_source='derived'"):
                validate_load(driver, {}, database=database)
    finally:
        with neo4j_driver() as (driver, database):
            with driver.session(database=database) as session:
                session.run("MATCH (n {id: 'bls:soc:29-1100'}) DETACH DELETE n")


def test_this_suite_owns_its_own_constraints_rather_than_inheriting_them(
    loaded: dict[str, int],
) -> None:
    """A duplicate constraint under IF NOT EXISTS is a silent no-op, not a failure.

    So a suite that declared its uniqueness on a shared canonical label would
    quietly depend on whichever suite got there first, and dropping *that*
    suite's constraint would remove this one's guarantee with no message
    anywhere. Every schema object here names a Bls-prefixed label; this asserts
    the ones that actually exist in the database, not just the statements.
    """
    with neo4j_driver() as (driver, database):
        with driver.session(database=database) as session:
            rows = list(
                session.run(
                    "SHOW CONSTRAINTS YIELD name, labelsOrTypes "
                    "WHERE name STARTS WITH 'bls_' RETURN name, labelsOrTypes"
                )
            )
    assert rows
    for row in rows:
        for label in row["labelsOrTypes"]:
            assert label.startswith("Bls"), (row["name"], label)
