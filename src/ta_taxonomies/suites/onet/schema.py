"""Neo4j schema setup for the O*NET suite (constraints and indexes).

Use case: run once at the start of a load so ids are unique per label,
preferred titles are range-indexed for exact lookups, and title text is
full-text indexed for the alias and substring tiers of Locate. Idempotent
(``IF NOT EXISTS``).

Two things this file does on purpose, both learned from ESCO rather than
rediscovered:

* **Constraints and indexes hang off the suite-scoped concrete label**
  (``:OnetOccupation``), never the shared canonical one (``:Occupation``).
  ESCO already owns a uniqueness constraint on ``:Occupation(id)``; Neo4j 5
  refuses a second equivalent constraint under a different name, so a suite
  that indexed the canonical label could not be loaded into a graph that
  already held ESCO.
* **The full-text index ships with the first load, not after a regression.**
  A query for an alias (``$q IN n.alt_labels``) or a case-insensitive title
  (``toLower(n.pref_label) = …``) cannot use a range index — the first indexes
  the list rather than its elements, the second wraps the property in a
  function — so both scan every node. O*NET makes that worse than it was for
  ESCO: its alias pool is 57k lay titles spread over 1,016 occupations.
"""

from __future__ import annotations

from neo4j import Driver

from ta_taxonomies.suites.onet.config import (
    FULLTEXT_ANALYZER,
    FULLTEXT_INDEX,
    LABEL_ONET_NODE,
    SEARCHABLE_LABELS,
)

CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT onet_node_id IF NOT EXISTS "
    f"FOR (n:{LABEL_ONET_NODE}) REQUIRE n.id IS UNIQUE",
] + [
    f"CREATE CONSTRAINT onet_{label.lower()}_id IF NOT EXISTS "
    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
    for label in SEARCHABLE_LABELS
]

# Range index per concrete label, reachable only from a MATCH on that same
# concrete label — matching the umbrella and filtering with labels(n) leaves
# the planner no way to it.
INDEXES: list[str] = [
    f"CREATE INDEX onet_{label.lower()}_pref IF NOT EXISTS FOR (n:{label}) ON (n.pref_label)"
    for label in SEARCHABLE_LABELS
]

# The O*NET-SOC code is what every other O*NET table joins on and what a
# crosswalk arrives holding, so resolving one must be a seek.
CODE_INDEXES: list[str] = [
    f"CREATE INDEX onet_{label.lower()}_code IF NOT EXISTS FOR (n:{label}) ON (n.code)"
    for label in SEARCHABLE_LABELS
]

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

ALL_STATEMENTS: list[str] = CONSTRAINTS + INDEXES + CODE_INDEXES + FULLTEXT_INDEXES


def apply_schema(driver: Driver, database: str | None = None) -> None:
    """Create constraints and indexes (idempotent)."""
    with driver.session(database=database) as session:
        for stmt in ALL_STATEMENTS:
            session.run(stmt)
