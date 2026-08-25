"""SFIA suite constants shared by the loader and tools (no I/O).

Use case: one place for Neo4j labels, relationship type names, search ``kind``
aliases, the declared Locate confidence policy, the declared path-scoring
policies, and — first, because it governs everything else — the **licence
guard** that decides which fields a node is even allowed to carry.

Why it exists: SFIA differs from ESCO and O\\*NET in three ways that must be
stated once and reused rather than re-decided per call site.

1. **Most of SFIA may not be stored here.** SFIA's free licence covers personal
   and internal use; redistributing SFIA material to another organisation needs
   a fee-bearing licence, and a public Apache-2.0 repository is redistribution
   to everyone (TA-workspace ADR-0006 §2). So this suite stores only what is
   factual and unprotected — skill codes, skill names, level numbers, and
   structure — and never SFIA's descriptive text. ``ALLOWED_NODE_KEYS`` below
   is that rule expressed as code rather than as a comment somebody can miss.
2. **There are no occupations.** SFIA is a skills-and-levels framework, so it
   cannot answer "what skills does job X need". What it has and nothing else in
   the adopted set does is a **responsibility axis**: seven ordered levels, and
   the published fact of which levels each skill is defined at.
3. **Its skill codes are four bare letters.** ``ISCO`` here is *Information
   systems coordination* and has nothing to do with the ILO's ISCO occupation
   classification that ESCO aligns to (ADR-0006 §5). Suite-scoped ids and
   ``source`` / ``source_id`` on every node are what keep the two apart.
"""

from __future__ import annotations

from ta_taxonomies.contract.models import PolicyRef

SOURCE = "sfia"

# The SFIA version this suite is written against. SFIA renames, splits and
# retires skill codes between versions, so a graph is only meaningful next to
# the version that produced it. (SFIA 10 is in public consultation as of
# 2026-08; it is not this.)
VERSION = "9"

# --- Licence guard (read this before adding a property) ---------------------
#
# ADR-0006 §2 permits storing SFIA's codes, names, level numbers and structure.
# It forbids storing SFIA's descriptive text: the skill description, the
# "essence of the level", the per-skill-per-level definitions, and the guidance
# notes. Those are fetched at runtime by users holding their own SFIA access.
#
# A comment saying "don't add descriptions" is an assertion with no assert. So
# node rows are built against an **allowlist**: a key outside this set stops the
# load. Adding a licensed field therefore cannot happen by absent-mindedly
# threading one more value through normalize — it requires editing this tuple,
# which is a visible, reviewable act with this comment attached to it.
ALLOWED_NODE_KEYS: tuple[str, ...] = (
    "id",
    "source",
    "source_id",
    "pref_label",
    "code",
    "kind",
    "version",
    "extra",
)

# Second, independent mechanism, because an allowlist only checks *where* text
# is put and not *what* it is: no stored string may be longer than a label
# plausibly is. The longest SFIA 9 skill name is 44 characters; the shortest
# sentence of SFIA prose in the source is far above this ceiling. A description
# smuggled into `pref_label` fails here even though the key is allowed.
MAX_LABEL_CHARS = 120

# Keys that name licensed prose in the source, refused wherever they appear in
# a normalized row — including inside `extra`, which the allowlist would
# otherwise wave through. Checked case-insensitively as substrings so
# `level_description`, `skillDescription` and `essence_of_the_level` are all
# caught by the stem.
LICENSED_FIELD_MARKERS: tuple[str, ...] = (
    "description",
    "definition",
    "essence",
    "guidance",
    "level_text",
    "leveltext",
    "notes",
    "statement",
    "summary",
    "narrative",
    "body",
    "prose",
    "text",
)

# --- Local snapshot layout --------------------------------------------------
# SFIA publishes no public bulk download, so `--mode full` reads a local
# snapshot the contributor fetched under their own licence (see fetch.py).
# The snapshot is gitignored; only extraction output ever reaches the graph.
SNAPSHOT_INDEX_FILE = "all-skills-a-z.html"
SNAPSHOT_SKILLS_DIR = "skills"
# A licensee holding SFIA's spreadsheet distribution can export it to this
# instead; the columns are documented in README.md. It carries the category
# tree, which the public HTML pages do not.
SNAPSHOT_STRUCTURE_CSV = "structure.csv"

# --- Graph shape ------------------------------------------------------------
#
# Every node carries three labels, each with a distinct job:
#   :SfiaNode        umbrella — one unique-id constraint for mixed-kind lookups
#   :Sfia<Kind>      concrete + suite-scoped — what search MATCHes on
#   :<CanonicalKind> shared vocabulary from ARCHITECTURE.md — cross-suite reads
#
# The suite prefix on the concrete label is not decoration, and the reason is
# not the one it is easy to assume. A second uniqueness constraint on a label
# another suite already constrained does **not** fail: with `IF NOT EXISTS` it
# is a silent no-op, so this suite would inherit ESCO's schema objects and
# `DROP CONSTRAINT esco_skill_id` would remove SFIA's uniqueness guarantee with
# no message anywhere. Measured, not assumed — see NOTES.md.
LABEL_SFIA_NODE = "SfiaNode"
LABEL_SKILL = "SfiaSkill"
LABEL_LEVEL = "SfiaLevel"
LABEL_CATEGORY = "SfiaCategory"
LABEL_SUBCATEGORY = "SfiaSubcategory"

CANONICAL_LABELS: dict[str, str] = {
    LABEL_SKILL: "Skill",
    LABEL_LEVEL: "Level",
    LABEL_CATEGORY: "SkillGroup",
    LABEL_SUBCATEGORY: "SkillGroup",
}

SEARCHABLE_LABELS: tuple[str, ...] = (
    LABEL_SKILL,
    LABEL_LEVEL,
    LABEL_CATEGORY,
    LABEL_SUBCATEGORY,
)

# Relationship types — the ARCHITECTURE.md canonical vocabulary, not SFIA's own
# wording, because `get_neighbors` refuses a type the suite does not declare and
# every caller in TA-agents speaks the canonical names.
REL_HAS_LEVEL = "HAS_LEVEL"
REL_BROADER_THAN = "BROADER_THAN"
REL_RELATED_TO = "RELATED_TO"
REL_MAY_LEAD_TO = "MAY_LEAD_TO"

TRAVERSABLE_RELS: frozenset[str] = frozenset(
    {
        REL_HAS_LEVEL,
        REL_BROADER_THAN,
        REL_RELATED_TO,
        REL_MAY_LEAD_TO,
    }
)

# --- Levels of responsibility ----------------------------------------------
# SFIA's seven levels are framework structure, not rows of data: they exist
# whatever subset of skills is loaded, so the loader materialises all seven
# every time rather than deriving them from whichever skills it happened to see.
#
# SFIA's own *names* for its levels are deliberately NOT
# stored. ADR-0006 §2 authorises "level numbers"; the names are SFIA's wording,
# and where this suite is unsure whether something is factual or descriptive the
# rule is to leave it out. A level's label here is "Level 4".
LEVEL_MIN = 1
LEVEL_MAX = 7
LEVELS: tuple[int, ...] = tuple(range(LEVEL_MIN, LEVEL_MAX + 1))

# SFIA skill codes are exactly four letters, uppercase, with no separators.
SKILL_CODE_LENGTH = 4

# kind filter values accepted by search_nodes
KIND_ALIASES: dict[str, str] = {
    "skill": LABEL_SKILL,
    "skills": LABEL_SKILL,
    "level": LABEL_LEVEL,
    "levels": LABEL_LEVEL,
    "responsibility": LABEL_LEVEL,
    "category": LABEL_CATEGORY,
    "categories": LABEL_CATEGORY,
    "subcategory": LABEL_SUBCATEGORY,
    "subcategories": LABEL_SUBCATEGORY,
    "skillgroup": LABEL_CATEGORY,
    "skill_group": LABEL_CATEGORY,
}

# --- Locate confidence policy (declared; NOT source data) -------------------
#
# These numbers are not comparable with ESCO's or O*NET's, and nothing should
# merge two suites' candidate lists by sorting on `confidence`. The structural
# reasons here are SFIA's own:
#
#   * Granularity. SFIA describes the whole digital profession in 147 skills;
#     ESCO uses 13,939. The same "0.95" resolves to a far coarser thing.
#   * There is no alias pool at all. ESCO ships curated altLabels and O*NET
#     ships 57k lay job titles; SFIA publishes one name per skill. So this
#     suite has no `exact_alt` tier — not a lower-confidence one, none — and a
#     user's own wording reaches the substring tier or nothing.
#   * The code is a first-class thing users type. "PROG level 4" is how SFIA is
#     spoken, so the four-letter code tier is tried first and scored as identity.
#
# A bare float cannot carry any of that, so every search result names the policy
# that produced it in `meta["confidence_policy"]`.
CONFIDENCE_POLICY = PolicyRef(name="sfia-locate-confidence", version="1")

CONF_EXACT_CODE = 0.99  # the four-letter skill code — identity, not similarity
CONF_EXACT_PREF = 0.95
CONF_CASEFOLD_UNIQUE = 0.90
CONF_CASEFOLD_AMBIGUOUS = 0.80
CONF_CONTAINS = 0.60

# --- Locate result policy (declared) ---------------------------------------
# SEARCH_LIMIT is what the caller gets; SEARCH_SCAN_CAP bounds how many matches
# are counted before the total itself is reported as capped. A bounded answer
# that does not say it was bounded reads as a complete one.
SEARCH_LIMIT = 25
SEARCH_SCAN_CAP = 5_000

# --- Full-text index -------------------------------------------------------
# `toLower(n.pref_label) = …` wraps the property in a function and cannot use a
# range index, so the case-insensitive and substring tiers would scan the whole
# graph. SFIA's own node count is tiny (147 skills), but the graph it lives in
# is not: the index is scoped to this suite's labels and keeps those tiers off a
# scan that grows with every other suite loaded beside it.
FULLTEXT_INDEX = "sfia_node_text"
# 'standard' strips English stop words, which would make a name containing one
# ("Learning and development management") unreachable through the index while a
# scan still found it.
FULLTEXT_ANALYZER = "standard-no-stop-words"
# Below this length an infix wildcard expands over most of the term dictionary
# and costs more than the scan it replaces; those queries take the scan path.
MIN_WILDCARD_TERM = 3

# --- Traversal safety policy -----------------------------------------------
# Every level node is an extreme hub by construction: seven levels carry every
# HAS_LEVEL edge in the graph, so level 4 alone links to most of the framework.
# That is the same shape that made O*NET's descriptor elements dangerous, and it
# needs the same two protections (per-type cap, and an arrival exempt from it).
MAX_PATH_DEPTH = 6
MAX_PATHS = 100
MAX_FRONTIER_PATHS = 500

# The branching cap is applied **per relationship type**. SFIA has no edge
# weight to order by — only HAS_LEVEL carries a number, and it is a level rather
# than a strength — so under one global cap the ordering would decide which
# whole relationship types survive. Per type, each kind of hop keeps its own 15.
MAX_BRANCHING_PER_REL = 15

# --- The essential/optional binary: deliberately absent ---------------------
#
# TA-agents filters neighbours on `properties["relation_type"] == kind` and its
# phrase router defaults that kind to "essential", so a suite whose edges carry
# no `relation_type` answers *nothing* to the commonest question there is —
# silently, because an empty list is a valid answer.
#
# The O*NET suite handles that by projecting its published Importance onto the
# binary. **This suite must not.** SFIA publishes no occupation-to-skill edges
# at all, and its levels measure responsibility, not how essential a skill is to
# a job: a level-7 HAS_LEVEL edge does not mean "essential", it means the skill
# is defined for someone setting strategy. Projecting one onto the other would
# invent a claim SFIA does not make, which is the move ADR-0006 forbids.
#
# So the value stays absent and the *absence* is made visible instead: this
# policy is named in `meta` and a `relation_type_absent` warning rides on any
# result carrying edges an ESCO-shaped filter would silently drop.
RELATION_TYPE_POLICY = PolicyRef(name="sfia-no-essential-projection", version="1")

# --- Path scoring policies (declared) --------------------------------------
#
# SFIA publishes no edge weights. What it does publish is an **ordinal**: the
# level numbers 1–7, and which of them each skill is defined at. Turning that
# ordering into a path score is a modelling decision of ours, not source data,
# so the policies are named and versioned and an unknown one is refused.
#
# Read the score as "how senior is this route", never as "how strong is this
# match". They are different questions and only the first is answerable here.
POLICY_LEVEL_BOTTLENECK = PolicyRef(name="sfia-level-bottleneck", version="1")
POLICY_LEVEL_PEAK = PolicyRef(name="sfia-level-peak", version="1")
POLICY_LEVEL_MEAN = PolicyRef(name="sfia-level-mean", version="1")

SUPPORTED_POLICIES: dict[str, PolicyRef] = {
    POLICY_LEVEL_BOTTLENECK.name: POLICY_LEVEL_BOTTLENECK,
    POLICY_LEVEL_PEAK.name: POLICY_LEVEL_PEAK,
    POLICY_LEVEL_MEAN.name: POLICY_LEVEL_MEAN,
}

# A hop that carries no level (BROADER_THAN, RELATED_TO). 0.5 is the neutral
# midpoint, as in the O*NET suite — but with a caveat that suite does not have:
# on a 1–7 scale normalised to [0, 1], 0.5 *is* level 4. A structural hop
# therefore scores exactly like a level-4 hop. That is a declared consequence,
# not a hidden one: the alternative values all say something worse (0.0 reads as
# "measured and found worthless", excluding the hop would shorten the evidence
# and raise a bottleneck score). Results report how many hops it applied to.
UNWEIGHTED_EDGE_SCORE = 0.5
