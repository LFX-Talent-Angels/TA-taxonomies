"""Neo4j schema setup for the BLS suite (constraints and indexes).

Use case: run once at the start of a load so ids are unique per label, titles
and codes are range-indexed for exact lookups, and title text is full-text
indexed for the alias and substring tiers of Locate. Idempotent
(``IF NOT EXISTS``).

Three things this file does on purpose, all inherited from what the ESCO and
O*NET suites measured rather than rediscovered:

* **Every schema object hangs off a suite-prefixed label**, never the shared
  canonical one (``:Occupation``). ``suites/onet/NOTES.md`` measured what
  happens otherwise, and it is worse than a failure: a second uniqueness
  constraint on a label another suite already constrains is a **silent no-op**
  under ``IF NOT EXISTS``. The suite then believes it owns a constraint it does
  not, and ``DROP CONSTRAINT esco_occupation_id`` would remove this suite's
  uniqueness guarantee with no message anywhere.
* **The SOC code index is not optional.** A crosswalk arrives holding a SOC
  code, not a title, and resolving one is the single most common operation
  against this suite. It must be a seek.
* **The full-text index ships with the first load, not after a regression.**
  ``$q IN n.alt_labels`` indexes the list rather than its elements and
  ``toLower(n.pref_label)`` wraps the property in a function; both scan every
  node. PR #9 measured ~6× on a release-scale graph.
"""

from __future__ import annotations

from neo4j import Driver

from ta_taxonomies.suites.bls.config import (
    FULLTEXT_ANALYZER,
    FULLTEXT_INDEX,
    LABEL_BLS_NODE,
    SEARCHABLE_LABELS,
)

CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT bls_node_id IF NOT EXISTS FOR (n:{LABEL_BLS_NODE}) REQUIRE n.id IS UNIQUE",
] + [
    f"CREATE CONSTRAINT bls_{label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
    for label in SEARCHABLE_LABELS
]

# Range index per concrete label. Reachable only from a MATCH on that same
# concrete label — matching the umbrella and filtering with `labels(n)` leaves
# the planner no route to it, which is the shape PR #9 replaced.
INDEXES: list[str] = [
    f"CREATE INDEX bls_{label.lower()}_pref IF NOT EXISTS FOR (n:{label}) ON (n.pref_label)"
    for label in SEARCHABLE_LABELS
]

CODE_INDEXES: list[str] = [
    f"CREATE INDEX bls_{label.lower()}_code IF NOT EXISTS FOR (n:{label}) ON (n.code)"
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
