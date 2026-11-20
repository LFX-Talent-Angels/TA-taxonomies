"""SFIA suite constants shared by the loader and tools (no I/O).

Use case: single place for Neo4j labels, relationship type names, search
``kind`` aliases, and Locate confidence scores so load.py and tools.py never
drift.

Why it exists: renaming a label or confidence policy should be one edit.
Import-only — works the same for Docker and Aura backends.

Licensing: SFIA prose (skill descriptions, level descriptions, guidance notes)
is never stored. Nodes carry structure only — codes, names, level numbers,
category slugs — plus an ``sfia_ref`` citation and a ``url`` pointer to the
SFIA website. Store the pointer, fetch the payload at runtime.
"""

from __future__ import annotations

from typing import Final

SOURCE: Final = "sfia"
FRAMEWORK: Final = "SFIA 9"

# Pointers (not payloads): where each skill's licensed text lives.
SKILL_URL_BASE = "https://sfia-online.org/en/sfia-9/skills/"
# The public "skills at a glance" page fetch.py reads (structure only).
GLANCE_URL = "https://sfia-online.org/en/sfia-9/sfia-views/full-framework-view/?path=/glance"

# Node labels stored in Neo4j
LABEL_SFIA_NODE = "SfiaNode"
LABEL_SKILL = "Skill"
LABEL_SKILL_LEVEL = "SkillLevel"
LABEL_SUBCATEGORY = "Subcategory"
LABEL_CATEGORY = "Category"

# Responsibility levels a skill can be defined at.
MIN_LEVEL = 1
MAX_LEVEL = 7

# Relationship types (suite-canonical; map onto ARCHITECTURE vocabulary)
# (Skill)-[:HAS_LEVEL]->(SkillLevel); one per level the skill is defined at
REL_HAS_LEVEL = "HAS_LEVEL"
# (narrower)-[:BROADER_THAN]->(broader), same convention as ESCO:
# Skill -> Subcategory -> Category
REL_BROADER_THAN = "BROADER_THAN"

TRAVERSABLE_RELS: frozenset[str] = frozenset(
    {
        REL_HAS_LEVEL,
        REL_BROADER_THAN,
    }
)

# kind filter values accepted by search_nodes
KIND_ALIASES: dict[str, str] = {
    "skill": LABEL_SKILL,
    "skills": LABEL_SKILL,
    "level": LABEL_SKILL_LEVEL,
    "levels": LABEL_SKILL_LEVEL,
    "skilllevel": LABEL_SKILL_LEVEL,
    "skill_level": LABEL_SKILL_LEVEL,
    "skilllevels": LABEL_SKILL_LEVEL,
    "category": LABEL_CATEGORY,
    "categories": LABEL_CATEGORY,
    "subcategory": LABEL_SUBCATEGORY,
    "subcategories": LABEL_SUBCATEGORY,
}

# Locate confidence policy (declared; not source data). Same scale as every
# other suite: it describes HOW the match was made, not how probable it is, and
# never carries a Lucene or cosine score.
CONF_EXACT_PREF = 0.95
CONF_EXACT_ALT = 0.90
CONF_CASEFOLD_UNIQUE = 0.85
CONF_CASEFOLD_AMBIGUOUS = 0.80
CONF_CONTAINS = 0.70
CONF_HYBRID = 0.75

# Locate result policy (declared). SEARCH_LIMIT is what the caller gets back;
# SEARCH_SCAN_CAP bounds how many matches are counted before the total is
# reported as capped.
SEARCH_LIMIT = 25
SEARCH_SCAN_CAP = 5_000

# Full-text index used by the alias/label branches of Locate. SFIA has no
# alias lists, so in practice it indexes pref_label only; the shared Locate
# tiers coalesce a missing alt_labels to [] and simply find no alias hits.
FULLTEXT_INDEX = "sfia_node_text"
FULLTEXT_ANALYZER = "standard-no-stop-words"

# Wildcard terms shorter than this take the scan path (see esco/config.py).
MIN_WILDCARD_TERM = 3

# Traversal safety policy. Copied from ESCO: SFIA's graph is small and
# tree-like (147 skills, 672 levels), so these caps are never the binding
# constraint, but bounded traversal is a contract rule, not a size decision.
MAX_PATH_DEPTH = 6
MAX_PATHS = 100
MAX_BRANCHING = 25
MAX_FRONTIER_PATHS = 500
