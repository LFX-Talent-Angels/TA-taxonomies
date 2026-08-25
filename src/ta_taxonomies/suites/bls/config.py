"""BLS/SOC suite constants shared by the loader and tools (no I/O).

Use case: one place for Neo4j labels, relationship types, search ``kind``
aliases, the declared Locate confidence policy, and the declared path-scoring
policies, so load.py and tools.py never drift.

Why this suite is shaped differently from ESCO and O*NET, stated once:

**BLS has no skills layer.** ADR-0006 adopts it as *"the occupation spine and
employment projections"*, and that is the whole of it — there is no
occupation→skill edge to load, because BLS does not publish one. A caller that
asks this suite "what skills does X need" gets an honest ``no_neighbors``, and
the answer is to ask O*NET or ESCO and arrive back here through a crosswalk.

What it has instead, and nothing else adopted does:

* **The SOC spine itself.** O*NET derives ``onet:soc:15-1252`` from its own
  code prefix and says so (``suites/onet/ids.py``: *"the authoritative SOC
  nodes belong to the BLS suite"*). This is that suite. The four SOC levels are
  first-class nodes with BLS's own titles and definitions, so a crosswalk that
  lands on a detailed code can be rolled up to a level no other suite has.
* **Numbers about the labour market**, not about a job's content: employment
  now, employment projected ten years out, annual openings, median wage, the
  self-employed share, and the same figures split across 420 industries.
"""

from __future__ import annotations

from ta_taxonomies.contract.models import PolicyRef

SOURCE = "bls"

# The Employment Projections round this suite is written against. BLS reissues
# the projections annually and the wage year moves independently of the
# projection window, so both are stamped on every node: a graph is only
# meaningful next to the round that produced it.
#
# These are read back from ``ep.dates`` at load time and the load fails if they
# disagree — see ``load.check_release``. Hard-coding them here and *checking*
# is the difference between a constant that governs and one that decorates.
RELEASE = "2024-34"
BASE_YEAR = 2024
PROJECTION_YEAR = 2034
WAGE_YEAR = 2024

# The SOC edition the BLS occupation codes belong to. BLS is the publisher of
# SOC, so unlike O*NET's derived view this is the taxonomy itself.
SOC_TAXONOMY = "2018 SOC"

# --- Node labels -----------------------------------------------------------
#
# Three labels per node, each with a distinct job:
#   :BlsNode        umbrella — one unique-id constraint, and what MERGE keys on
#   :Bls<Kind>      concrete + suite-scoped — what search MATCHes on
#   :<CanonicalKind> ARCHITECTURE.md vocabulary — cross-suite reads
#
# The suite prefix on the concrete label is load-bearing and the reason is the
# one measured in ``suites/onet/NOTES.md``: a second uniqueness constraint on a
# label another suite already constrains does **not** fail under
# ``IF NOT EXISTS`` — it is a silent no-op, and the suite then depends on a
# schema object it does not own and cannot see.
LABEL_BLS_NODE = "BlsNode"
LABEL_OCCUPATION = "BlsOccupation"  # detailed SOC (NN-NNNN, last digit non-zero)
LABEL_SOC_GROUP = "BlsSocGroup"  # major / minor / broad SOC groups
LABEL_INDUSTRY = "BlsIndustry"  # National Employment Matrix industries

# Canonical labels applied alongside the suite-scoped ones.
#
# ``LABEL_INDUSTRY`` is deliberately absent. ARCHITECTURE.md's canonical node
# vocabulary is Skill · Task · Occupation · Framework · Level · Evidence — it
# has no kind for the economic dimension, and an industry is not any of those.
# Borrowing the nearest one would put BLS industries into whatever a
# cross-suite reader means by ``:Framework``. Industries therefore carry only
# suite-scoped labels and announce their kind through ``Node.kind``, which the
# contract documents as "Canonical **or suite** kind". See NOTES.md.
CANONICAL_LABELS: dict[str, str | None] = {
    LABEL_OCCUPATION: "Occupation",
    LABEL_SOC_GROUP: "SOCGroup",
    LABEL_INDUSTRY: None,
}

SEARCHABLE_LABELS: tuple[str, ...] = (
    LABEL_OCCUPATION,
    LABEL_SOC_GROUP,
    LABEL_INDUSTRY,
)

# --- Relationship types ----------------------------------------------------
REL_BROADER_THAN = "BROADER_THAN"  # canonical: child SOC → nearest ancestor
# Not canonical, for the same reason :BlsIndustry is not: the shared edge
# vocabulary (HAS_SKILL · PERFORMS_TASK · BROADER_THAN · RELATED_TO ·
# HAS_LEVEL · SUPPORTED_BY · MAY_LEAD_TO) has nothing that means "this many
# people in this occupation work in this industry". RELATED_TO would carry the
# employment matrix as an untyped association and lose the only thing it says.
REL_EMPLOYED_IN = "EMPLOYED_IN"

TRAVERSABLE_RELS: frozenset[str] = frozenset({REL_BROADER_THAN, REL_EMPLOYED_IN})

# --- SOC code structure ----------------------------------------------------
# SOC codes are NN-NNNN and the digits *are* the hierarchy, exactly as
# published in the SOC coding structure: 11-0000 major, 11-1000 minor,
# 11-1010 broad, 11-1011 detailed. This is a documented decomposition, not an
# inference, which is why ids.py can build the tree with no join table.
SOC_LEVEL_MAJOR = "major"
SOC_LEVEL_MINOR = "minor"
SOC_LEVEL_BROAD = "broad"
SOC_LEVEL_DETAILED = "detailed"
SOC_LEVELS: tuple[str, ...] = (
    SOC_LEVEL_MAJOR,
    SOC_LEVEL_MINOR,
    SOC_LEVEL_BROAD,
    SOC_LEVEL_DETAILED,
)

# BLS's own economy-wide total. It is a SOC-shaped code but not an occupation,
# and it is the parent of nothing — every real major group is a root.
TOTAL_ALL_OCCUPATIONS = "00-0000"

# --- National Employment Matrix aggregate "industries" ---------------------
# Three ind_codes in ep.series are not industries at all; they are
# economy-wide totals and class-of-worker splits. Loading them as :BlsIndustry
# would put "Self-employed workers" beside "Crop production" in any answer to
# "which industries employ X".
IND_TOTAL_ALL = "TE1000"  # Total, all industries → the occupation's own totals
IND_SELF_EMPLOYED = "TE1100"  # Self-employed workers
IND_WAGE_SALARY = "TE1200"  # Total wage and salary employment
CLASS_OF_WORKER_CODES: dict[str, str] = {
    IND_SELF_EMPLOYED: "self_employed",
    IND_WAGE_SALARY: "wage_and_salary",
}
AGGREGATE_IND_CODES: frozenset[str] = frozenset(
    {IND_TOTAL_ALL, *CLASS_OF_WORKER_CODES},
)

# --- ep.aspect codes -------------------------------------------------------
#
# ``ep.aspect`` keys every value by a two-character ``aspect_type`` and the
# ``ep/`` directory ships **no lookup file for it** — unlike ep.eductrn,
# ep.otjt and ep.wkex, which all have one. So the meanings below were not read
# off a code list; each was established against the data and is re-checked at
# load time by ``load.verify_aspect_semantics``:
#
#   PR  projected employment      — ``ep.dates.proj_year``
#   A1  employment change, numeric — PR − base holds on 113,473 of 113,473 rows
#   A2  employment change, percent — A1 / base × 100 within published rounding
#   A3  percent self-employed      — 62.9 for Writers and authors, 0.5 for
#                                    Registered nurses, matching the published
#                                    self-employment shares
#   A4  annual average openings    — 115.2k for Software developers, matching
#                                    the published occupational openings table
#   A5  median annual wage         — 133,080 / 93,600 / 30,480 for Software
#                                    developers / Registered nurses / Fast food
#                                    workers, matching the published medians
#   A6  industry share of the occupation's employment, base year
#   A7  occupation share of the industry's employment, base year
#   A8  industry share of the occupation's employment, projected year
#   A9  occupation share of the industry's employment, projected year
#
# A1 and A2 are verified arithmetically on every load. A3–A5 are checked for
# being present only on the total-all-industries series (833 of them) and in
# range; their *meaning* rests on the spot checks above and is recorded in
# NOTES.md so a reader can re-run it rather than take it on trust.
ASPECT_PROJECTED = "PR"
ASPECT_CHANGE_NUMERIC = "A1"
ASPECT_CHANGE_PERCENT = "A2"
ASPECT_PCT_SELF_EMPLOYED = "A3"
ASPECT_OPENINGS = "A4"
ASPECT_MEDIAN_WAGE = "A5"
ASPECT_IND_SHARE_BASE = "A6"
ASPECT_OCC_SHARE_BASE = "A7"
ASPECT_IND_SHARE_PROJ = "A8"
ASPECT_OCC_SHARE_PROJ = "A9"

# Aspects BLS publishes only on the total-all-industries series.
OCCUPATION_ONLY_ASPECTS: frozenset[str] = frozenset(
    {ASPECT_PCT_SELF_EMPLOYED, ASPECT_OPENINGS, ASPECT_MEDIAN_WAGE}
)

# Employment is published in thousands of jobs; wages in whole dollars per
# year. Stated here because "1693.8" is meaningless without it and because
# scaling it to persons would invent precision the source does not have.
EMPLOYMENT_UNIT = "thousands of jobs"
WAGE_UNIT = "USD per year"

# Percentage aspects must lie in [0, 100]; a share outside it means the file
# changed shape. Employment change percent is *not* bounded — an occupation can
# more than double — so it is deliberately excluded from this check.
SHARE_MIN, SHARE_MAX = 0.0, 100.0

# --- search kinds ----------------------------------------------------------
KIND_ALIASES: dict[str, str] = {
    "occupation": LABEL_OCCUPATION,
    "occupations": LABEL_OCCUPATION,
    "soc": LABEL_SOC_GROUP,
    "socgroup": LABEL_SOC_GROUP,
    "soc_group": LABEL_SOC_GROUP,
    "group": LABEL_SOC_GROUP,
    "groups": LABEL_SOC_GROUP,
    "industry": LABEL_INDUSTRY,
    "industries": LABEL_INDUSTRY,
    "sector": LABEL_INDUSTRY,
}

# --- Locate confidence policy (declared; NOT source data) ------------------
#
# Not comparable with ESCO's or O*NET's numbers, and nothing should merge three
# suites' candidate lists by sorting on ``confidence``. The reason here is
# different again from O*NET's granularity argument:
#
#   * A SOC code is an *identity BLS assigns*, so an exact code hit is as
#     certain as this suite gets — hence CONF_EXACT_CODE above every title tier.
#   * The alias pool is 5,820 "lay titles" over 817 occupations, published by
#     BLS itself as the everyday names for a SOC line. That is a curated list,
#     not O*NET's deliberately idiosyncratic 57k, so an exact alias hit is
#     stronger evidence here (0.88) than there (0.82) — again for a structural
#     reason and not because it was tuned.
#   * A group title matches at the same confidence as an occupation title, but
#     resolves to something much coarser. ``kind`` is how a caller says which
#     it wanted; ``ambiguous`` is how the result says it could be either.
#
# Every search result names this policy in ``meta["confidence_policy"]``, so a
# fusion layer can see the scales are unreconciled instead of assuming they are.
CONFIDENCE_POLICY = PolicyRef(name="bls-locate-confidence", version="1")

CONF_EXACT_CODE = 0.99
CONF_EXACT_PREF = 0.95
CONF_EXACT_ALT = 0.88
CONF_CASEFOLD_UNIQUE = 0.85
CONF_CASEFOLD_AMBIGUOUS = 0.78
CONF_CONTAINS = 0.65

# The SOC-code tier accepts a code carried by another suite's identifier
# (``onet:soc:15-1252``). That is a string identity on the SOC code and not a
# semantic mapping, so it is reported under its own method and its own
# confidence rather than folded into exact_code — see ``resolve_soc``.
CONF_FOREIGN_SOC_CODE = 0.97

SEARCH_LIMIT = 25
SEARCH_SCAN_CAP = 5_000

# --- Full-text index -------------------------------------------------------
# Same reasoning as O*NET's, inherited rather than rediscovered: the alias tier
# (`$q IN n.alt_labels`) and the case-insensitive tier (`toLower(...)`) cannot
# use a range index, and PR #9 measured what matching an umbrella label and
# filtering with `labels(n)` costs — the planner cannot reach a per-label index
# from there. Concrete labels from the first load; full text for the rest.
FULLTEXT_INDEX = "bls_node_text"
FULLTEXT_ANALYZER = "standard-no-stop-words"
MIN_WILDCARD_TERM = 3

# --- Traversal safety policy -----------------------------------------------
# The hubs here are industries, and they are worse than O*NET's elements:
# "Total, all industries" is excluded, but a summary industry such as
# "Educational services" still carries an EMPLOYED_IN edge from most of the
# 1,113 occupations. The per-relationship-type cap and the exemption for a hop
# that lands on the target are both taken from O*NET's measured findings.
MAX_PATH_DEPTH = 6
MAX_PATHS = 100
MAX_FRONTIER_PATHS = 500
MAX_BRANCHING_PER_REL = 15

# --- Path scoring policies (declared) --------------------------------------
#
# Backed by published numbers, like O*NET's and unlike ESCO's. What is policy
# is how a column of BLS percentages becomes one number per path.
POLICY_SHARE_BOTTLENECK = PolicyRef(name="bls-employment-share-bottleneck", version="1")
POLICY_SHARE_MEAN = PolicyRef(name="bls-employment-share-mean", version="1")
POLICY_PROJECTED_GROWTH = PolicyRef(name="bls-projected-growth", version="1")

SUPPORTED_POLICIES: dict[str, PolicyRef] = {
    POLICY_SHARE_BOTTLENECK.name: POLICY_SHARE_BOTTLENECK,
    POLICY_SHARE_MEAN.name: POLICY_SHARE_MEAN,
    POLICY_PROJECTED_GROWTH.name: POLICY_PROJECTED_GROWTH,
}

# A hop with no published number — every BROADER_THAN edge, since SOC's tree is
# structure rather than measurement. 0.5 is the neutral midpoint: rolling up to
# a parent group is neither evidence for a route nor against it.
UNWEIGHTED_EDGE_SCORE = 0.5

# ``bls-projected-growth`` needs a window, because percent change is unbounded
# above and floored at −100. The window below is a **declared modelling
# decision**, not a BLS statistic: ±50% over the ten-year projection window maps
# linearly onto [0, 1], so no change scores 0.5 and anything beyond it clamps.
# It is symmetric and round because it has to be explicable, not because 50 was
# fitted. Measured against the 2024–34 round afterwards, which is the honest
# order to report it in:
#
#   * All 1,113 occupation totals fall inside it — the extremes are Wind
#     turbine service technicians at +49.9% and Word processors and typists at
#     −36.1%. At occupation level the clamp never fires.
#   * 260 of the 110,353 occupation×industry cells (0.2%) fall outside, up to
#     +208.9%. Those are small cells in fast-moving industries, and the clamp
#     does fire there. Scoring reports the count in its warnings rather than
#     letting a clamped value read as a measured one.
GROWTH_WINDOW_PCT = 50.0
