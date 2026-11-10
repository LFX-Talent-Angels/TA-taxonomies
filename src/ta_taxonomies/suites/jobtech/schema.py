"""Neo4j schema setup for the JobTech suite (constraints and indexes).

Use case: run once at the start of a load so ``JobtechNode.id`` is unique,
``pref_label`` is range-indexed per label for exact lookups, and label text is
full-text indexed for alias/casefold/word-start lookups. Idempotent
(``IF NOT EXISTS``).

``Occupation`` and ``Skill`` are canonical labels shared with the other suites.
Their ``id`` constraints are declared here too, so a database holding only JobTech
is still protected; where another suite already made an equivalent one,
``IF NOT EXISTS`` makes the statement a no-op. Full-text is
``FOR (n:JobtechNode)`` so Locate retrieval stays suite-local.
"""

from __future__ import annotations

from neo4j import Driver

from ta_taxonomies.suites.jobtech.config import (
    FULLTEXT_ANALYZER,
    FULLTEXT_INDEX,
    LABEL_JOBTECH_NODE,
    LABEL_OCCUPATION,
    LABEL_SKILL,
    LABEL_SKILL_HEADLINE,
    LABEL_SSYK_GROUP,
)

SEARCHABLE_LABELS = (LABEL_OCCUPATION, LABEL_SKILL, LABEL_SSYK_GROUP, LABEL_SKILL_HEADLINE)


def _name(label: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in label.lower())


CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT jobtech_node_id IF NOT EXISTS "
    f"FOR (n:{LABEL_JOBTECH_NODE}) REQUIRE n.id IS UNIQUE",
] + [
    f"CREATE CONSTRAINT jobtech_{_name(label)}_id IF NOT EXISTS "
    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
    for label in SEARCHABLE_LABELS
]

INDEXES: list[str] = [
    f"CREATE INDEX jobtech_{_name(label)}_pref IF NOT EXISTS FOR (n:{label}) ON (n.pref_label)"
    for label in SEARCHABLE_LABELS
] + [
    # Codes are looked up before any label (Locate tier 0).
    f"CREATE INDEX jobtech_node_code IF NOT EXISTS FOR (n:{LABEL_JOBTECH_NODE}) ON (n.code)",
]

FULLTEXT_INDEXES: list[str] = [
    f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
    f"FOR (n:{LABEL_JOBTECH_NODE}) "
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
