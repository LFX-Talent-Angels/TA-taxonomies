"""ESCO suite constants shared by the loader and tools (no I/O).

Use case: single place for Neo4j labels, relationship type names, search
``kind`` aliases, and Locate confidence scores so load.py and tools.py never
drift.

Why it exists: renaming a label or confidence policy should be one edit.
Import-only — works the same for Docker and Aura backends.
"""

from __future__ import annotations

SOURCE = "esco"

# Node labels stored in Neo4j
LABEL_ESCO_NODE = "EscoNode"
LABEL_OCCUPATION = "Occupation"
LABEL_SKILL = "Skill"
LABEL_ISCO_GROUP = "ISCOGroup"
LABEL_SKILL_GROUP = "SkillGroup"

# Relationship types (suite-canonical; map onto ARCHITECTURE vocabulary)
# HAS_SKILL carries relation_type = essential | optional
REL_HAS_SKILL = "HAS_SKILL"
# (narrower)-[:BROADER_THAN]->(broader)
REL_BROADER_THAN = "BROADER_THAN"
# (Occupation)-[:CLASSIFIED_UNDER]->(ISCOGroup)
REL_CLASSIFIED_UNDER = "CLASSIFIED_UNDER"
# skill ↔ skill (essential/optional in source)
REL_RELATED_TO = "RELATED_TO"

TRAVERSABLE_RELS: frozenset[str] = frozenset(
    {
        REL_HAS_SKILL,
        REL_BROADER_THAN,
        REL_CLASSIFIED_UNDER,
        REL_RELATED_TO,
    }
)

# kind filter values accepted by search_nodes
KIND_ALIASES: dict[str, str] = {
    "occupation": LABEL_OCCUPATION,
    "occupations": LABEL_OCCUPATION,
    "skill": LABEL_SKILL,
    "skills": LABEL_SKILL,
    "isco": LABEL_ISCO_GROUP,
    "iscogroup": LABEL_ISCO_GROUP,
    "isco_group": LABEL_ISCO_GROUP,
    "skillgroup": LABEL_SKILL_GROUP,
    "skill_group": LABEL_SKILL_GROUP,
    "skillgroups": LABEL_SKILL_GROUP,
}

# Locate confidence policy (declared; not source data).
# The scale describes HOW the match was made, not how probable it is. The
# full-text index changed how candidates are *retrieved*; it deliberately did
# not touch these numbers, and it must never feed a relevance score into them
# (Lucene scores are not comparable across queries, so a "0.83" derived from
# one would not mean the same thing twice).
CONF_EXACT_PREF = 0.95
CONF_EXACT_ALT = 0.90
CONF_CASEFOLD_UNIQUE = 0.85
CONF_CASEFOLD_AMBIGUOUS = 0.80
CONF_CONTAINS = 0.70

# Locate result policy (declared). SEARCH_LIMIT is what the caller gets back;
# SEARCH_SCAN_CAP bounds how many matches we are willing to count before
# reporting the total as capped. ARCHITECTURE requires bounded tools to report
# what they cut, so search_nodes returns both numbers instead of silently
# handing back the first 25 of an unknown many.
SEARCH_LIMIT = 25
SEARCH_SCAN_CAP = 5_000

# Full-text index used by the alias/label branches of Locate.
# Analyzer: 'standard-no-stop-words' rather than the default 'standard'.
# The default strips English stop words, which would make an exact label like
# "one to one communication" unfindable through the index while the old scan
# found it — a recall regression the confidence scale could not express.
FULLTEXT_INDEX = "esco_node_text"
FULLTEXT_ANALYZER = "standard-no-stop-words"

# Wildcard terms this short expand over most of the term dictionary, so the
# index costs more than the scan it replaces. Queries containing one take the
# scan path: never slower than before, never a different answer.
MIN_WILDCARD_TERM = 3

# Traversal safety policy. ESCO contains high-degree hub skills, so traversal
# expands one hop at a time and prunes deterministically before the next hop.
MAX_PATH_DEPTH = 6
MAX_PATHS = 100
MAX_BRANCHING = 25
MAX_FRONTIER_PATHS = 500
