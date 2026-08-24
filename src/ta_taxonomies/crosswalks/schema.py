"""Neo4j constraints and indexes owned by the crosswalk layer (structure only).

Use case: run once before loading correspondences so provenance nodes and
recorded absences have unique keys.

Why it exists separately from each suite's schema: the crosswalk layer owns
nodes no suite owns, and must not touch the suites' constraints. In particular
it does not re-declare uniqueness on shared labels like ``:Occupation(id)``,
which ESCO already constrains as ``esco_occupation_id``.

Worth being precise about why, because the obvious reason is wrong. A second
constraint with a different name over the same label/property pair does *not*
fail when written with ``IF NOT EXISTS`` -- measured on Neo4j 5.26.29, it is a
silent no-op and only the first constraint exists afterwards. (Without
``IF NOT EXISTS`` it is a hard ``ConstraintAlreadyExists``, but every
``schema.py`` in this repo uses the flag.) So the failure mode is quieter and
worse than an error: a second writer believes it declared its own guarantee,
silently inherits the first one's, and a later ``DROP CONSTRAINT
esco_occupation_id`` removes a uniqueness guarantee that something else was
relying on, with no signal anywhere. Each owner declaring constraints only on
labels it owns is what keeps that from happening.

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
