"""Neo4j schema setup for the ESCO suite (constraints and indexes).

Use case: run once at the start of a load so ``id`` and ``uri`` are unique per
label, ``pref_label`` is range-indexed for exact lookups, and label text is
full-text indexed for alias/substring lookups. Idempotent (``IF NOT EXISTS``).

Why it exists: protects identity under MERGE and speeds Locate-style lookups.
Does not insert taxonomy rows — structure only, before load.py merges data.

Note on the range indexes: they only pay off if the query matches the
*concrete* label (``:Occupation``), not the umbrella ``:EscoNode`` with a
``labels(n)`` filter — from the umbrella the planner cannot reach them and
falls back to a full label scan. tools.py matches concrete labels for exactly
this reason.
"""

from __future__ import annotations

from neo4j import Driver

from ta_taxonomies.suites.esco.config import (
    FULLTEXT_ANALYZER,
    FULLTEXT_INDEX,
    LABEL_ESCO_NODE,
    LABEL_ISCO_GROUP,
    LABEL_OCCUPATION,
    LABEL_SKILL,
    LABEL_SKILL_GROUP,
)

SEARCHABLE_LABELS = (LABEL_OCCUPATION, LABEL_SKILL, LABEL_ISCO_GROUP, LABEL_SKILL_GROUP)

# Constraints: uniqueness on suite-scoped id (and uri for provenance joins)
CONSTRAINTS: list[str] = (
    [
        f"CREATE CONSTRAINT esco_node_id IF NOT EXISTS "
        f"FOR (n:{LABEL_ESCO_NODE}) REQUIRE n.id IS UNIQUE",
    ]
    + [
        f"CREATE CONSTRAINT esco_{label.lower()}_id IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        for label in SEARCHABLE_LABELS
    ]
    + [
        f"CREATE CONSTRAINT esco_{label.lower()}_uri IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.uri IS UNIQUE"
        for label in SEARCHABLE_LABELS
    ]
)

INDEXES: list[str] = [
    f"CREATE INDEX esco_{label.lower()}_pref IF NOT EXISTS FOR (n:{label}) ON (n.pref_label)"
    for label in SEARCHABLE_LABELS
]

# Full-text index over the two properties users actually type at: the preferred
# label and the alias list. A range index cannot serve those lookups —
# ``$q IN n.alt_labels`` indexes the whole list, not its elements, and
# ``toLower(n.pref_label) = …`` wraps the property in a function — so both used
# to scan every node. Array properties are indexed element-wise here.
#
# eventually_consistent stays false: the loader validates immediately after
# MERGE, and a lagging index would make those assertions flaky.
FULLTEXT_INDEXES: list[str] = [
    f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
    f"FOR (n:{'|'.join(SEARCHABLE_LABELS)}) "
    "ON EACH [n.pref_label, n.alt_labels] "
    "OPTIONS { indexConfig: { "
    f"`fulltext.analyzer`: '{FULLTEXT_ANALYZER}', "
    "`fulltext.eventually_consistent`: false } }"
]


def apply_schema(driver: Driver, database: str | None = None) -> None:
    """Create constraints and indexes (idempotent)."""
    with driver.session(database=database) as session:
        for stmt in CONSTRAINTS + INDEXES + FULLTEXT_INDEXES:
            session.run(stmt)
