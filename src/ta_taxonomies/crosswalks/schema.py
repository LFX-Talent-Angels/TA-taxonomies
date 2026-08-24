"""Neo4j constraints and indexes owned by the crosswalk layer (structure only).

Use case: run once before loading correspondences so provenance nodes and
recorded absences have unique keys.

Why it exists separately from each suite's schema: the crosswalk layer owns
nodes no suite owns, and must not touch the suites' constraints. It also must
not try to re-declare uniqueness on labels a suite already constrained --
Neo4j 5 rejects a second constraint with a different name over the same
label/property pair, which is exactly what would happen if this module tried
to be helpful about ``:Occupation(id)``. It stays in its own lane.

Idempotent (``IF NOT EXISTS``). Inserts no correspondence rows.
"""

from __future__ import annotations

from neo4j import Driver

from ta_taxonomies.crosswalks.config import LABEL_CROSSWALK_SOURCE, LABEL_NO_LINK

CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT crosswalk_source_key IF NOT EXISTS "
    f"FOR (n:{LABEL_CROSSWALK_SOURCE}) REQUIRE n.key IS UNIQUE",
    f"CREATE CONSTRAINT crosswalk_no_link_id IF NOT EXISTS "
    f"FOR (n:{LABEL_NO_LINK}) REQUIRE n.id IS UNIQUE",
]

INDEXES: list[str] = [
    f"CREATE INDEX crosswalk_no_link_source IF NOT EXISTS "
    f"FOR (n:{LABEL_NO_LINK}) ON (n.checked_against)",
]


def apply_schema(driver: Driver, database: str | None = None) -> None:
    """Create the crosswalk layer's constraints and indexes (idempotent)."""
    with driver.session(database=database) as session:
        for statement in CONSTRAINTS + INDEXES:
            session.run(statement)
