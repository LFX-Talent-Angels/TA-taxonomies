"""BLS suite constants shared by the fetcher, loader and tools (no I/O).

Use case: one place for Neo4j labels, relationship names, search ``kind``
aliases, the skill-score thresholds and Locate confidences, so fetch.py,
load.py and tools.py never drift.

What BLS is here: the US occupation spine (SOC 2018), with Employment
Projections (2025-35) facts on each occupation — wage, outlook, entry
education, experience, training — and the 17 broad EP skills, scored 1-5 per
occupation. All of it is US federal data in the public domain. The EP skill
scores are derived from O*NET importance ratings (CC BY 4.0), so O*NET is
credited wherever they are shown.
"""

from __future__ import annotations

from typing import Final

SOURCE: Final = "bls"

#: What the loaded data is, for citations and the README.
SOC_VERSION = "SOC 2018"
PROJECTION_CYCLE = "2025-2035"
WAGE_PERIOD = "May 2025"

# Node labels stored in Neo4j
LABEL_BLS_NODE = "BlsNode"
LABEL_OCCUPATION = "Occupation"
LABEL_SOC_GROUP = "SOCGroup"
LABEL_SKILL = "Skill"
LABEL_EDUCATION = "EducationLevel"
LABEL_TRAINING = "TrainingLevel"

# Relationship types (suite-canonical; same names as ESCO where they mean the same)
# (Occupation)-[:HAS_SKILL {score, rating, relation_type}]->(Skill)
REL_HAS_SKILL = "HAS_SKILL"
# (Occupation)-[:CLASSIFIED_UNDER]->(SOCGroup), the broad group
REL_CLASSIFIED_UNDER = "CLASSIFIED_UNDER"
# (narrower)-[:BROADER_THAN]->(broader), same convention as ESCO and SFIA:
# broad -> minor -> major
REL_BROADER_THAN = "BROADER_THAN"
# (Occupation)-[:REQUIRES_EDUCATION]->(EducationLevel), typical entry education
REL_REQUIRES_EDUCATION = "REQUIRES_EDUCATION"
# (Occupation)-[:REQUIRES_TRAINING]->(TrainingLevel), typical on-the-job training
REL_REQUIRES_TRAINING = "REQUIRES_TRAINING"

TRAVERSABLE_RELS: frozenset[str] = frozenset(
    {
        REL_HAS_SKILL,
        REL_CLASSIFIED_UNDER,
        REL_BROADER_THAN,
        REL_REQUIRES_EDUCATION,
        REL_REQUIRES_TRAINING,
    }
)

# kind filter values accepted by search_nodes
KIND_ALIASES: dict[str, str] = {
    "occupation": LABEL_OCCUPATION,
    "occupations": LABEL_OCCUPATION,
    "skill": LABEL_SKILL,
    "skills": LABEL_SKILL,
    "soc group": LABEL_SOC_GROUP,
    "soc_group": LABEL_SOC_GROUP,
    "socgroup": LABEL_SOC_GROUP,
    "group": LABEL_SOC_GROUP,
    "education": LABEL_EDUCATION,
    "training": LABEL_TRAINING,
}

# EP skill scores (1-5, the mean O*NET importance of the skill's elements) and
# BLS's own words for each band. Thresholds are declared Talent Angels policy:
# "Very important" and up is essential, "Important" is optional, and anything
# lower is not a skill the occupation is said to need, so no edge is written.
ESSENTIAL_MIN_SCORE = 3.5
OPTIONAL_MIN_SCORE = 2.5
#: (lower bound, BLS rating) from high to low; see the EP skills methodology.
SCORE_RATINGS: tuple[tuple[float, str], ...] = (
    (4.5, "Extremely important"),
    (3.5, "Very important"),
    (2.5, "Important"),
    (1.5, "Somewhat important"),
    (0.0, "Not important"),
)

#: EP "typical education needed for entry" categories, lowest first. The
#: position is the node's ``rank``. A value not listed here means BLS changed
#: its categories, and the load stops rather than guess.
EDUCATION_LEVELS: tuple[str, ...] = (
    "No formal educational credential",
    "High school diploma or equivalent",
    "Some college, no degree",
    "Postsecondary nondegree award",
    "Associate's degree",
    "Bachelor's degree",
    "Master's degree",
    "Doctoral or professional degree",
)
#: EP "typical on-the-job training" categories, shortest first. BLS's "None"
#: (no training needed) gets no node and no edge.
TRAINING_LEVELS: tuple[str, ...] = (
    "Short-term on-the-job training",
    "Moderate-term on-the-job training",
    "Long-term on-the-job training",
    "Apprenticeship",
    "Internship/residency",
)
NO_TRAINING = "None"

#: SOC 2018 major groups, keyed by the first two digits of a SOC code. Used to
#: name the groups of a broad search (public US federal classification). The
#: graph also holds them as SOCGroup nodes; this copy saves a query.
SOC_MAJOR_GROUPS: dict[str, str] = {
    "11": "Management",
    "13": "Business and Financial Operations",
    "15": "Computer and Mathematical",
    "17": "Architecture and Engineering",
    "19": "Life, Physical, and Social Science",
    "21": "Community and Social Service",
    "23": "Legal",
    "25": "Educational Instruction and Library",
    "27": "Arts, Design, Entertainment, Sports, and Media",
    "29": "Healthcare Practitioners and Technical",
    "31": "Healthcare Support",
    "33": "Protective Service",
    "35": "Food Preparation and Serving Related",
    "37": "Building and Grounds Cleaning and Maintenance",
    "39": "Personal Care and Service",
    "41": "Sales and Related",
    "43": "Office and Administrative Support",
    "45": "Farming, Fishing, and Forestry",
    "47": "Construction and Extraction",
    "49": "Installation, Maintenance, and Repair",
    "51": "Production",
    "53": "Transportation and Material Moving",
    "55": "Military Specific",
}

# Locate confidence policy (declared; not source data). Same scale as every
# other suite: it says HOW the match was made, never a Lucene or cosine score.
CONF_EXACT_PREF = 0.95
CONF_EXACT_ALT = 0.90
CONF_CASEFOLD_UNIQUE = 0.85
CONF_CASEFOLD_AMBIGUOUS = 0.80
CONF_CONTAINS = 0.70
CONF_HYBRID = 0.75

SEARCH_LIMIT = 25
SEARCH_SCAN_CAP = 5_000

FULLTEXT_INDEX = "bls_node_text"
FULLTEXT_ANALYZER = "standard-no-stop-words"
VECTOR_INDEX = "bls_label_embedding"
MIN_WILDCARD_TERM = 3

# Traversal safety policy (same caps as the other suites).
MAX_PATH_DEPTH = 6
MAX_PATHS = 100
MAX_BRANCHING = 25
MAX_FRONTIER_PATHS = 500
