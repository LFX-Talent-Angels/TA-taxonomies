"""O*NET suite constants shared by the loader and tools (no I/O).

Use case: single place for Neo4j labels, relationship type names, search
``kind`` aliases, and Locate confidence scores so load.py and tools.py never
drift.

Why it exists: renaming a label or confidence policy should be one edit.
Import-only — works the same for Docker and Aura backends.
"""

from __future__ import annotations

SOURCE = "onet"

LABEL_ONET_NODE = "OnetNode"
LABEL_OCCUPATION = "Occupation"
LABEL_SKILL = "Skill"
LABEL_KNOWLEDGE = "Knowledge"
LABEL_ABILITY = "Ability"
LABEL_WORK_ACTIVITY = "WorkActivity"
LABEL_WORK_CONTEXT = "WorkContext"
LABEL_WORK_STYLE = "WorkStyle"
LABEL_TASK = "Task"
LABEL_SOFTWARE = "Software"
LABEL_JOB_ZONE = "JobZone"
LABEL_INTEREST = "Interest"
LABEL_SCALE = "Scale"
LABEL_DETAILED_WORK_ACTIVITY = "DetailedWorkActivity"
LABEL_INTERMEDIATE_WORK_ACTIVITY = "IntermediateWorkActivity"

REL_HAS_SKILL = "HAS_SKILL"
REL_HAS_KNOWLEDGE = "HAS_KNOWLEDGE"
REL_HAS_ABILITY = "HAS_ABILITY"
REL_HAS_WORK_ACTIVITY = "HAS_WORK_ACTIVITY"
REL_HAS_WORK_CONTEXT = "HAS_WORK_CONTEXT"
REL_HAS_WORK_STYLE = "HAS_WORK_STYLE"
REL_PERFORMS_TASK = "PERFORMS_TASK"
REL_USES_SOFTWARE = "USES_SOFTWARE"
REL_RELATED_TO = "RELATED_TO"
REL_BROADER_THAN = "BROADER_THAN"
REL_HAS_JOB_ZONE = "HAS_JOB_ZONE"
REL_HAS_INTEREST = "HAS_INTEREST"
REL_HAS_EDUCATION = "HAS_EDUCATION"
REL_HAS_TRAINING = "HAS_TRAINING"
REL_HAS_TASK_RATING = "HAS_TASK_RATING"

TRAVERSABLE_RELS: frozenset[str] = frozenset(
    {
        REL_HAS_SKILL,
        REL_HAS_KNOWLEDGE,
        REL_HAS_ABILITY,
        REL_HAS_WORK_ACTIVITY,
        REL_HAS_WORK_CONTEXT,
        REL_HAS_WORK_STYLE,
        REL_PERFORMS_TASK,
        REL_USES_SOFTWARE,
        REL_RELATED_TO,
        REL_BROADER_THAN,
        REL_HAS_JOB_ZONE,
        REL_HAS_INTEREST,
        REL_HAS_EDUCATION,
        REL_HAS_TRAINING,
        REL_HAS_TASK_RATING,
    }
)

KIND_ALIASES: dict[str, str] = {
    "occupation": LABEL_OCCUPATION,
    "occupations": LABEL_OCCUPATION,
    "skill": LABEL_SKILL,
    "skills": LABEL_SKILL,
    "knowledge": LABEL_KNOWLEDGE,
    "ability": LABEL_ABILITY,
    "abilities": LABEL_ABILITY,
    "task": LABEL_TASK,
    "tasks": LABEL_TASK,
    "software": LABEL_SOFTWARE,
    "workactivity": LABEL_WORK_ACTIVITY,
    "work_activity": LABEL_WORK_ACTIVITY,
    "workcontext": LABEL_WORK_CONTEXT,
    "work_context": LABEL_WORK_CONTEXT,
    "workstyle": LABEL_WORK_STYLE,
    "work_style": LABEL_WORK_STYLE,
    "jobzone": LABEL_JOB_ZONE,
    "job_zone": LABEL_JOB_ZONE,
    "interest": LABEL_INTEREST,
}

# Locate confidence policy (declared; not source data). Same scale as ESCO so
# a full-text index never feeds Lucene scores into confidence.
CONF_EXACT_PREF = 0.95
CONF_EXACT_ALT = 0.90
CONF_CASEFOLD_UNIQUE = 0.85
CONF_CASEFOLD_AMBIGUOUS = 0.80
CONF_CONTAINS = 0.70

SEARCH_LIMIT = 25
SEARCH_SCAN_CAP = 5_000

FULLTEXT_INDEX = "onet_node_text"
FULLTEXT_ANALYZER = "standard-no-stop-words"
MIN_WILDCARD_TERM = 3

MAX_PATH_DEPTH = 6
MAX_PATHS = 100
MAX_BRANCHING = 25
MAX_FRONTIER_PATHS = 500
