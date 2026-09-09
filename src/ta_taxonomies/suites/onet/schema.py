"""Neo4j schema setup for the O*NET suite (constraints and indexes).

Use case: run once at the start of a load so ``OnetNode.id`` is unique,
``pref_label`` is range-indexed on O*NET-specific labels, and label text is
full-text indexed for alias/substring lookups. Idempotent (``IF NOT EXISTS``).

Occupation and Skill already have unique ``id`` + ``pref_label`` indexes from
ESCO (shared canonical labels). This module does not recreate those; it adds
``:OnetNode`` identity and indexes ESCO does not own.

Full-text is ``FOR (n:OnetNode)`` so Locate retrieval stays suite-local even
though Occupation/Skill labels are shared.
"""

from __future__ import annotations

from neo4j import Driver

from ta_taxonomies.suites.onet.config import (
    FULLTEXT_ANALYZER,
    FULLTEXT_INDEX,
    LABEL_ABILITY,
    LABEL_DETAILED_WORK_ACTIVITY,
    LABEL_INTEREST,
    LABEL_INTERMEDIATE_WORK_ACTIVITY,
    LABEL_JOB_ZONE,
    LABEL_KNOWLEDGE,
    LABEL_ONET_NODE,
    LABEL_SCALE,
    LABEL_SOFTWARE,
    LABEL_TASK,
    LABEL_WORK_ACTIVITY,
    LABEL_WORK_CONTEXT,
    LABEL_WORK_STYLE,
)

# Labels ESCO does not constrain. Occupation/Skill uniqueness already exists.
ONET_OWNED_LABELS = (
    LABEL_TASK,
    LABEL_SOFTWARE,
    LABEL_KNOWLEDGE,
    LABEL_ABILITY,
    LABEL_WORK_ACTIVITY,
    LABEL_WORK_CONTEXT,
    LABEL_WORK_STYLE,
    LABEL_JOB_ZONE,
    LABEL_INTEREST,
    LABEL_SCALE,
    LABEL_DETAILED_WORK_ACTIVITY,
    LABEL_INTERMEDIATE_WORK_ACTIVITY,
)


def _constraint_name(label: str) -> str:
    return "onet_" + "".join(ch if ch.isalnum() else "_" for ch in label.lower()) + "_id"


CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT onet_node_id IF NOT EXISTS "
    f"FOR (n:{LABEL_ONET_NODE}) REQUIRE n.id IS UNIQUE",
] + [
    f"CREATE CONSTRAINT {_constraint_name(label)} IF NOT EXISTS "
    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
    for label in ONET_OWNED_LABELS
]

INDEXES: list[str] = [
    f"CREATE INDEX onet_{label.lower()}_pref IF NOT EXISTS FOR (n:{label}) ON (n.pref_label)"
    for label in ONET_OWNED_LABELS
]

FULLTEXT_INDEXES: list[str] = [
    f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
    f"FOR (n:{LABEL_ONET_NODE}) "
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
