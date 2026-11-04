"""JobTech suite constants shared by the fetcher, loader and tools (no I/O).

What JobTech is here: the Swedish labour-market taxonomy published by
Arbetsförmedlingen (JobTech Taxonomy, CC0), pinned to one version. It holds
occupation names grouped under SSYK 2012, skills grouped under skill
headlines, the skills each SSYK unit group asks for, the few direct
essential/optional skills JobTech gives an occupation, how far one occupation
can substitute for another, and JobTech's own matches to ESCO.

Labels are Swedish. JobTech's ESCO matches are stored as URIs (data, not
shared nodes), which is what lets an English question reach a Swedish
occupation through ESCO without the two suites sharing anything.
"""

from __future__ import annotations

from typing import Final

SOURCE: Final = "jobtech"

#: The JobTech Taxonomy version this loader was built and pinned against.
TAXONOMY_VERSION = 31

# Node labels stored in Neo4j
LABEL_JOBTECH_NODE = "JobtechNode"
LABEL_OCCUPATION = "Occupation"
LABEL_SSYK_GROUP = "SSYKGroup"
LABEL_SKILL = "Skill"
LABEL_SKILL_HEADLINE = "SkillHeadline"

# Relationship types
# (Occupation|SSYKGroup)-[:HAS_SKILL {relation_type}]->(Skill)
#   relation_type: "essential" / "optional" (JobTech's own, occupation only) or
#   "group" (the occupation's SSYK unit group asks for the skill).
REL_HAS_SKILL = "HAS_SKILL"
# (Occupation)-[:CLASSIFIED_UNDER]->(SSYKGroup), the SSYK level-4 unit group
REL_CLASSIFIED_UNDER = "CLASSIFIED_UNDER"
# (narrower)-[:BROADER_THAN]->(broader): SSYK 4 -> 3 -> 2 -> 1; Skill -> SkillHeadline
REL_BROADER_THAN = "BROADER_THAN"
# (A:Occupation)-[:SUBSTITUTES {substitutability_pct}]->(B:Occupation):
# someone in A can do B's job to that degree (JobTech's substitutability).
REL_SUBSTITUTES = "SUBSTITUTES"

TRAVERSABLE_RELS: frozenset[str] = frozenset(
    {REL_HAS_SKILL, REL_CLASSIFIED_UNDER, REL_BROADER_THAN, REL_SUBSTITUTES}
)

#: Relation types of HAS_SKILL. "group" is not JobTech saying the occupation
#: needs the skill, only that its SSYK unit group does, so it counts as optional.
RELATION_ESSENTIAL = "essential"
RELATION_OPTIONAL = "optional"
RELATION_GROUP = "group"

KIND_ALIASES: dict[str, str] = {
    "occupation": LABEL_OCCUPATION,
    "occupations": LABEL_OCCUPATION,
    "yrke": LABEL_OCCUPATION,
    "skill": LABEL_SKILL,
    "skills": LABEL_SKILL,
    "kompetens": LABEL_SKILL,
    "ssyk group": LABEL_SSYK_GROUP,
    "ssyk_group": LABEL_SSYK_GROUP,
    "ssykgroup": LABEL_SSYK_GROUP,
    "group": LABEL_SSYK_GROUP,
    "skill headline": LABEL_SKILL_HEADLINE,
    "skill_headline": LABEL_SKILL_HEADLINE,
    "skillheadline": LABEL_SKILL_HEADLINE,
}

#: ESCO match kinds JobTech publishes, strongest first, as stored property names.
ESCO_MATCH_KINDS: tuple[str, ...] = ("exact", "close", "broad", "narrow")

CONF_EXACT_PREF = 0.95
CONF_EXACT_ALT = 0.90
CONF_CASEFOLD_UNIQUE = 0.85
CONF_CASEFOLD_AMBIGUOUS = 0.80
CONF_CONTAINS = 0.70
CONF_HYBRID = 0.75

SEARCH_LIMIT = 25
SEARCH_SCAN_CAP = 5_000

FULLTEXT_INDEX = "jobtech_node_text"
# Not the "swedish" analyzer: Locate's contains tier sends wildcard terms
# ("*bagare*"), which Lucene does not stem, so a stemmed index ("bagar")
# would miss them. Word-start matching does the same job here.
FULLTEXT_ANALYZER = "standard-no-stop-words"
VECTOR_INDEX = "jobtech_label_embedding"
MIN_WILDCARD_TERM = 3

MAX_PATH_DEPTH = 6
MAX_PATHS = 100
MAX_BRANCHING = 25
MAX_FRONTIER_PATHS = 500
