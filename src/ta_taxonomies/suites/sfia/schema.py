"""Neo4j schema setup for the SFIA suite (constraints and indexes).

Use case: run once at the start of a load so ``SfiaNode.id`` is unique,
``pref_label`` is range-indexed per label for exact lookups, and label text is
full-text indexed for casefold/substring lookups. Idempotent (``IF NOT EXISTS``).

Why it exists: protects identity under MERGE and speeds Locate-style lookups.
Does not insert taxonomy rows — structure only, before load.py merges data.

``Skill`` is a canonical label shared with ESCO and O*NET. Its ``id``
constraint and ``pref_label`` index are declared here too, so a database that
holds only SFIA is still protected; where another suite already created an
equivalent one, ``IF NOT EXISTS`` makes the statement a no-op. Full-text is
``FOR (n:SfiaNode)`` so Locate retrieval stays suite-local.
"""

from __future__ import annotations

from neo4j import Driver

from ta_taxonomies.suites.sfia.config import (
    FULLTEXT_ANALYZER,
    FULLTEXT_INDEX,
    LABEL_CATEGORY,
    LABEL_SFIA_NODE,
    LABEL_SKILL,
    LABEL_SKILL_LEVEL,
    LABEL_SUBCATEGORY,
)

SEARCHABLE_LABELS = (LABEL_SKILL, LABEL_SKILL_LEVEL, LABEL_SUBCATEGORY, LABEL_CATEGORY)

# Constraints: uniqueness on suite-scoped id (anchor label + each concrete label)
CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT sfia_node_id IF NOT EXISTS "
    f"FOR (n:{LABEL_SFIA_NODE}) REQUIRE n.id IS UNIQUE",
] + [
    f"CREATE CONSTRAINT sfia_{label.lower()}_id IF NOT EXISTS "
    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
    for label in SEARCHABLE_LABELS
]

INDEXES: list[str] = [
    f"CREATE INDEX sfia_{label.lower()}_pref IF NOT EXISTS FOR (n:{label}) ON (n.pref_label)"
    for label in SEARCHABLE_LABELS
]

# Same shape as ESCO's index (pref_label + alt_labels). SFIA nodes carry no
# alt_labels, which the index tolerates; keeping the property in the index
# means the shared Locate Cypher needs no SFIA special case.
FULLTEXT_INDEXES: list[str] = [
    f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
    f"FOR (n:{LABEL_SFIA_NODE}) "
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
