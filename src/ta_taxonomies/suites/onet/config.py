"""O*NET suite constants shared by the loader and tools (no I/O).

Use case: single place for Neo4j labels, relationship type names, search
``kind`` aliases, the declared Locate confidence policy, and the declared
path-scoring policies, so load.py and tools.py never drift.

Why it exists: O*NET differs from ESCO in two ways that must be stated once
and reused, not re-decided per call site — its identity is a *code* rather
than a URI, and its occupation-descriptor edges carry *real numbers*
(Importance / Level with sample size and 95% confidence bounds). Everything
that turns those numbers into a ranking is policy, and policy lives here
under a name and a version.
"""

from __future__ import annotations

from ta_taxonomies.contract.models import PolicyRef

SOURCE = "onet"

# The O*NET release this suite is written against. O*NET reissues quarterly and
# renumbers Element IDs across major taxonomy revisions, so a graph is only
# meaningful next to the release that produced it.
RELEASE = "30.3"
# The SOC taxonomy the O*NET-SOC codes extend, stamped onto every SOC group
# node. (The O*NET-SOC taxonomy version itself was declared here too and used
# nowhere; a constant with no reader states an identity the code does not
# actually carry, so it was removed rather than left implying one. The release
# above is the version that matters, and it is on every node.)
SOC_TAXONOMY = "2018 SOC"

# Node labels stored in Neo4j.
#
# Every node carries three labels, each with a distinct job:
#   :OnetNode       umbrella — one unique-id constraint for mixed-kind lookups
#   :Onet<Kind>     concrete + suite-scoped — what search MATCHes on
#   :<CanonicalKind> shared vocabulary from ARCHITECTURE.md — cross-suite reads
#
# The suite prefix on the concrete label is not decoration. ESCO already
# declares `REQUIRE n.id IS UNIQUE` on the bare :Occupation label
# (suites/esco/schema.py), and Neo4j 5 rejects a second, equivalent constraint
# under a different name. Prefixing gives this suite its own constraint and its
# own index without touching ESCO's, while the canonical label stays available
# to anything that wants "all occupations, whatever the source".
LABEL_ONET_NODE = "OnetNode"
LABEL_OCCUPATION = "OnetOccupation"
LABEL_ELEMENT = "OnetElement"
LABEL_ELEMENT_GROUP = "OnetElementGroup"
LABEL_TASK = "OnetTask"
LABEL_SOC_GROUP = "OnetSocGroup"

# Canonical labels applied alongside the suite-scoped ones.
CANONICAL_LABELS: dict[str, str] = {
    LABEL_OCCUPATION: "Occupation",
    LABEL_ELEMENT: "Skill",
    LABEL_ELEMENT_GROUP: "SkillGroup",
    LABEL_TASK: "Task",
    LABEL_SOC_GROUP: "SOCGroup",
}

SEARCHABLE_LABELS: tuple[str, ...] = (
    LABEL_OCCUPATION,
    LABEL_ELEMENT,
    LABEL_ELEMENT_GROUP,
    LABEL_TASK,
    LABEL_SOC_GROUP,
)

# Relationship types — the ARCHITECTURE.md canonical vocabulary, not O*NET's
# own wording. The Sprint 1 prototype called the descriptor edge
# REQUIRES_SKILL; every caller in TA-agents asks for HAS_SKILL, and
# get_neighbors rejects a type it does not recognise, so a suite that renamed
# it would answer "unknown_rel_types" to the only question anyone asks.
REL_HAS_SKILL = "HAS_SKILL"
REL_PERFORMS_TASK = "PERFORMS_TASK"
REL_CLASSIFIED_UNDER = "CLASSIFIED_UNDER"
REL_RELATED_TO = "RELATED_TO"
REL_BROADER_THAN = "BROADER_THAN"

TRAVERSABLE_RELS: frozenset[str] = frozenset(
    {
        REL_HAS_SKILL,
        REL_PERFORMS_TASK,
        REL_CLASSIFIED_UNDER,
        REL_RELATED_TO,
        REL_BROADER_THAN,
    }
)

# Content Model branches loaded as descriptor elements. All four share one file
# schema (Element ID + Scale ID + Data Value + N + SE + CI bounds) and all four
# answer "what does a person bring to this job", which is what HAS_SKILL means.
# Work Activities (4.*) and Work Context (5.*) describe the job rather than the
# person and are deliberately left out — see NOTES.md.
DOMAIN_ABILITY = "ability"
DOMAIN_BASIC_SKILL = "basic_skill"
DOMAIN_CROSS_FUNCTIONAL_SKILL = "cross_functional_skill"
DOMAIN_KNOWLEDGE = "knowledge"

# filename → (content model domain, element id prefix used to sanity-check rows)
RATED_FILES: dict[str, tuple[str, str]] = {
    "Abilities.txt": (DOMAIN_ABILITY, "1.A"),
    "Essential Skills.txt": (DOMAIN_BASIC_SKILL, "2.A"),
    "Transferable Skills.txt": (DOMAIN_CROSS_FUNCTIONAL_SKILL, "2.B"),
    "Knowledge.txt": (DOMAIN_KNOWLEDGE, "2.C"),
}

# O*NET rating scales. IM and LV are separate *rows* per (occupation, element);
# the loader pairs them onto one edge.
SCALE_IMPORTANCE = "IM"
SCALE_LEVEL = "LV"
# Published scale bounds (Scales Reference.txt). Hard-coded rather than read
# from the file because the scoring policies below are defined against these
# specific numbers; a silent scale change must break, not rescale.
IM_MIN, IM_MAX = 1.0, 5.0
LV_MIN, LV_MAX = 0.0, 7.0

# kind filter values accepted by search_nodes
KIND_ALIASES: dict[str, str] = {
    "occupation": LABEL_OCCUPATION,
    "occupations": LABEL_OCCUPATION,
    "skill": LABEL_ELEMENT,
    "skills": LABEL_ELEMENT,
    "element": LABEL_ELEMENT,
    "elements": LABEL_ELEMENT,
    "knowledge": LABEL_ELEMENT,
    "ability": LABEL_ELEMENT,
    "abilities": LABEL_ELEMENT,
    "skillgroup": LABEL_ELEMENT_GROUP,
    "skill_group": LABEL_ELEMENT_GROUP,
    "skillgroups": LABEL_ELEMENT_GROUP,
    "elementgroup": LABEL_ELEMENT_GROUP,
    "element_group": LABEL_ELEMENT_GROUP,
    "task": LABEL_TASK,
    "tasks": LABEL_TASK,
    "soc": LABEL_SOC_GROUP,
    "socgroup": LABEL_SOC_GROUP,
    "soc_group": LABEL_SOC_GROUP,
}

# --- Locate confidence policy (declared; NOT source data) -------------------
#
# These numbers are NOT comparable with ESCO's, and nothing should merge two
# suites' candidate lists by sorting on `confidence`. Two reasons, both
# structural rather than a matter of tuning:
#
#   * Granularity. O*NET describes the whole US labour market in 1,016
#     occupations; ESCO uses 3,039. An exact title hit in O*NET therefore
#     resolves to a coarser thing than the same hit in ESCO — same number,
#     less resolution.
#   * What an alias is. ESCO altLabels are curated synonyms of the preferred
#     label. O*NET's alias pool is 57,543 *lay* job titles plus 7,953 reported
#     titles — real things people call themselves, deliberately including
#     employer-specific and idiosyncratic ones. An exact hit on one is weaker
#     evidence than an exact hit on an ESCO altLabel, so CONF_EXACT_ALT sits
#     lower here (0.82) than ESCO's 0.90 for a *different reason* than tuning.
#
# Because a bare float cannot carry that, every search result names the policy
# that produced it in `meta["confidence_policy"]`. Comparing confidences across
# suites is legitimate only once someone writes a calibration that maps both
# onto one scale; until then the policy name is the signal that they are not
# on one.
CONFIDENCE_POLICY = PolicyRef(name="onet-locate-confidence", version="1")

CONF_EXACT_CODE = 0.99  # the O*NET-SOC code itself — identity, not similarity
CONF_EXACT_PREF = 0.95
CONF_EXACT_ALT = 0.82
CONF_CASEFOLD_UNIQUE = 0.85
CONF_CASEFOLD_AMBIGUOUS = 0.78
CONF_CONTAINS = 0.65

# --- Locate result policy (declared) ---------------------------------------
# SEARCH_LIMIT is what the caller gets; SEARCH_SCAN_CAP bounds how many matches
# are counted before the total itself is reported as capped. A bounded answer
# that does not say it was bounded reads as a complete one.
SEARCH_LIMIT = 25
SEARCH_SCAN_CAP = 5_000

# --- Full-text index -------------------------------------------------------
# The alias/substring tiers of Locate cannot use a range index: `$q IN
# n.alt_labels` indexes the list, not its elements, and `toLower(n.pref_label)`
# wraps the property in a function. Both fall back to a whole-graph scan.
#
# This suite ships the index from its first load rather than adding it after a
# regression, because O*NET's alias pool (57k titles on 1,016 nodes) makes the
# scan path far worse here than it ever was for ESCO.
FULLTEXT_INDEX = "onet_node_text"
# 'standard' strips English stop words, which would make an exact title like
# "First-Line Supervisors of Office and Administrative Support Workers"
# unreachable through the index while a scan still found it.
FULLTEXT_ANALYZER = "standard-no-stop-words"
# Below this length an infix wildcard expands over most of the term dictionary
# and costs more than the scan it replaces; those queries take the scan path.
MIN_WILDCARD_TERM = 3

# --- Traversal safety policy -----------------------------------------------
# O*NET's descriptor elements are extreme hubs by construction: 120 elements
# carry ~107k edges, so a single element such as "Reading Comprehension" links
# to 894 occupations. Branching must be capped harder than in ESCO.
MAX_PATH_DEPTH = 6
MAX_PATHS = 100
MAX_FRONTIER_PATHS = 500

# The branching cap is applied **per relationship type**, not per node, and
# that is not a refinement — a single cap does not work here.
#
# Expansion is ordered by Importance so that when the cap bites it keeps the
# strongest edges. But only HAS_SKILL carries an Importance; PERFORMS_TASK,
# RELATED_TO, CLASSIFIED_UNDER and BROADER_THAN have none, so under one global
# cap every unrated edge sorts last and is cut first. An occupation with 120
# rated descriptors would never expand a single RELATED_TO edge, and
# occupation-to-occupation routes — the ones Pathfind is for — would be
# unreachable while the tool reported a large, healthy `pruned` count.
#
# Per type, each kind of hop keeps its own strongest 15 and the ordering still
# does its job inside HAS_SKILL, where the numbers exist.
MAX_BRANCHING_PER_REL = 15

# --- HAS_SKILL binary compatibility policy (declared; NOT source data) ------
#
# O*NET has no notion of an "essential" skill. ESCO does, and TA-agents was
# written against ESCO: `reveal.py:94` keeps only edges whose
# `properties["relation_type"]` equals the requested kind, and the phrase
# router defaults that kind to "essential" for the most common question there
# is ("what skills does X need"). Against an O*NET graph with no
# `relation_type` on its edges, that filter matches nothing and the agent
# reports no skills — silently, because an empty list is a valid answer.
#
# So the loader writes a `relation_type` derived from Importance under the
# policy named below, and stamps `relation_type_policy` on the same edge so the
# value can never be mistaken for something O*NET published. Projecting a
# published number onto a coarser binary is lossy but honest; the opposite
# move — inventing a number from ESCO's binary — is the one ADR-0006 forbids.
#
# The threshold is the midpoint of the 1–5 Importance scale. It is a round
# number chosen for being explicable, not an empirical cut-off.
RELATION_TYPE_POLICY = PolicyRef(name="onet-essential-threshold", version="1")
ESSENTIAL_IMPORTANCE_MIN = 3.0

# --- Path scoring policies (declared) --------------------------------------
#
# Unlike ESCO, this is implementable from source data: every HAS_SKILL edge
# carries Importance and Level with a sample size, a standard error and 95%
# confidence bounds. What is *not* source data is how those become one number
# per path — hence three named policies rather than one unnamed default.
#
# All three normalise Importance to [0, 1] over the published scale and then
# combine per-path. Edges without a rating (PERFORMS_TASK, CLASSIFIED_UNDER,
# RELATED_TO, BROADER_THAN) contribute UNWEIGHTED_EDGE_SCORE, declared here so
# a structural hop is never silently free.
POLICY_BOTTLENECK = PolicyRef(name="onet-importance-bottleneck", version="1")
POLICY_MEAN = PolicyRef(name="onet-importance-mean", version="1")
POLICY_LOWER_CI = PolicyRef(name="onet-importance-lower-ci", version="1")

SUPPORTED_POLICIES: dict[str, PolicyRef] = {
    POLICY_BOTTLENECK.name: POLICY_BOTTLENECK,
    POLICY_MEAN.name: POLICY_MEAN,
    POLICY_LOWER_CI.name: POLICY_LOWER_CI,
}

# A hop that carries no published rating. 0.5 is the neutral midpoint: it
# neither rewards nor punishes routing through structure. Any other value would
# be a claim about how much a taxonomy edge is worth, which O*NET does not make.
UNWEIGHTED_EDGE_SCORE = 0.5

# O*NET marks a rating "Recommend Suppress = Y" when its reliability is too low
# to display, and O*NET OnLine hides those. Scoring replaces them with
# UNWEIGHTED_EDGE_SCORE rather than using a number the publisher disowns — and
# rather than deleting the hop, which under a minimum would *raise* the path's
# score for having unreliable data. 5,076 of 107,280 edges are flagged in
# release 30.3, and the count is reported in the result's warnings.
DROP_SUPPRESSED_IN_SCORING = True
