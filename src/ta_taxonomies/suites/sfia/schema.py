"""Neo4j schema setup for the SFIA suite (constraints and indexes).

Use case: run once at the start of a load so ids are unique per label, names and
codes are range-indexed for exact lookups, and name text is full-text indexed
for the case-insensitive and substring tiers of Locate. Idempotent
(``IF NOT EXISTS``).

Two things this file does on purpose, both measured rather than assumed:

* **Constraints and indexes hang off the suite-scoped concrete label**
  (``:SfiaSkill``), never the shared canonical one (``:Skill``). The reason is
  not that Neo4j would refuse a duplicate — with ``IF NOT EXISTS`` it does not
  refuse, it **silently creates nothing**, so this suite would inherit ESCO's
  constraint on ``:Skill(id)`` and a later ``DROP CONSTRAINT esco_skill_id``
  would remove SFIA's uniqueness guarantee with no message anywhere.
* **The full-text index ships with the first load.** ``toLower(n.pref_label) =
  …`` wraps the property in a function and cannot use a range index. SFIA's own
  147 skills would scan quickly; the graph they live in, holding four other
  suites, would not.
"""

from __future__ import annotations

from neo4j import Driver

from ta_taxonomies.suites.sfia.config import (
    FULLTEXT_ANALYZER,
    FULLTEXT_INDEX,
    LABEL_SFIA_NODE,
    SEARCHABLE_LABELS,
)

CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT sfia_node_id IF NOT EXISTS "
    f"FOR (n:{LABEL_SFIA_NODE}) REQUIRE n.id IS UNIQUE",
] + [
    f"CREATE CONSTRAINT sfia_{label.lower()}_id IF NOT EXISTS "
    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
    for label in SEARCHABLE_LABELS
]

# Range index per concrete label, reachable only from a MATCH on that same
# concrete label — matching the umbrella and filtering with labels(n) leaves the
# planner no way to it (PR #9).
INDEXES: list[str] = [
    f"CREATE INDEX sfia_{label.lower()}_pref IF NOT EXISTS FOR (n:{label}) ON (n.pref_label)"
    for label in SEARCHABLE_LABELS
]

# The four-letter skill code is what a SFIA user types and what a crosswalk
# arrives holding, so resolving one must be a seek rather than a scan.
CODE_INDEXES: list[str] = [
    f"CREATE INDEX sfia_{label.lower()}_code IF NOT EXISTS FOR (n:{label}) ON (n.code)"
    for label in SEARCHABLE_LABELS
]

# eventually_consistent stays false: the loader validates immediately after
# MERGE, and a lagging index would make those assertions flaky.
FULLTEXT_INDEXES: list[str] = [
    f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
    f"FOR (n:{'|'.join(SEARCHABLE_LABELS)}) "
    "ON EACH [n.pref_label] "
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
