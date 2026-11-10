"""Deterministic ESCO-scale synthetic graph for Locate benchmarks.

Use case: the committed fixture is 61 nodes, so it cannot show what
``search_nodes`` costs on the real classification (~21k nodes / ~161k edges).
This module builds a *document* in the same shape ``load.normalize_document``
consumes, so the benchmark exercises the real loader, the real schema and the
real tools — only the text is synthetic.

Why synthetic and not the real dump: ARCHITECTURE's "pointer, not payload" rule
forbids committing ESCO prose. Label text here is generated from a small
hand-written vocabulary; it reproduces the property that actually drives Locate
cost — heavy token overlap across tens of thousands of short labels — without
redistributing anything.

Deterministic: seeded ``random.Random``, so two runs produce the same graph and
before/after numbers are comparable.
"""

from __future__ import annotations

import random
from typing import Any

# Target composition, matching the English ESCO release closely enough that
# per-label index selectivity is realistic (see suites/esco/NOTES.md).
N_OCCUPATIONS = 3_039
N_SKILLS = 13_939
N_ISCO_GROUPS = 619
N_SKILL_GROUPS = 3_601  # occupation + skill pillar hierarchy concepts

N_HAS_SKILL = 129_000
N_BROADER_OCC = 3_000
N_BROADER_SKILL = 14_547
N_RELATED_TO = 11_600

_BASE = "http://data.europa.eu/esco"

# Vocabulary. Kept small on purpose: real taxonomies reuse a handful of head
# words ("data", "engineer", "manage") across thousands of concepts, and that
# reuse is exactly what makes substring search expensive and answers ambiguous.
_ROLES = [
    "engineer",
    "developer",
    "scientist",
    "analyst",
    "architect",
    "administrator",
    "manager",
    "consultant",
    "technician",
    "specialist",
    "officer",
    "designer",
    "researcher",
    "coordinator",
    "director",
    "operator",
    "inspector",
    "planner",
    "advisor",
    "supervisor",
]
_DOMAINS = [
    "data",
    "software",
    "network",
    "systems",
    "database",
    "security",
    "cloud",
    "web",
    "mobile",
    "machine learning",
    "embedded",
    "information",
    "digital",
    "application",
    "platform",
    "integration",
    "infrastructure",
    "business intelligence",
    "product",
    "quality assurance",
    "robotics",
    "telecommunications",
    "energy",
    "logistics",
    "manufacturing",
    "finance",
    "marketing",
    "healthcare",
    "education",
    "research",
    "operations",
    "compliance",
    "procurement",
    "maintenance",
    "safety",
    "environmental",
    "geospatial",
    "simulation",
    "automation",
    "computer vision",
]
_SECTORS = [
    "industrial",
    "marine",
    "aerospace",
    "automotive",
    "biomedical",
    "chemical",
    "civil",
    "electrical",
    "environmental",
    "mining",
    "nuclear",
    "petroleum",
    "railway",
    "textile",
    "agricultural",
    "food",
    "pharmaceutical",
    "renewable energy",
    "construction",
    "forestry",
    "maritime",
    "aviation",
    "defence",
    "public sector",
    "retail",
    "hospitality",
    "insurance",
    "banking",
    "media",
    "sports",
]
_VERBS = [
    "manage",
    "develop",
    "analyse",
    "design",
    "implement",
    "maintain",
    "monitor",
    "apply",
    "perform",
    "coordinate",
    "evaluate",
    "configure",
    "document",
    "test",
    "optimise",
    "supervise",
    "report",
    "validate",
    "integrate",
    "troubleshoot",
    "audit",
    "forecast",
    "calibrate",
    "deploy",
]
_OBJECTS = _DOMAINS + [
    "technical documentation",
    "project budgets",
    "test procedures",
    "user requirements",
    "risk assessments",
    "service levels",
    "training programmes",
    "supplier contracts",
    "safety protocols",
    "statistical models",
    "control systems",
    "quality standards",
]
_GROUP_HEADS = [
    "sciences",
    "technologies",
    "services",
    "processes",
    "management",
    "operations",
    "engineering",
    "analysis",
    "administration",
    "support",
]


def _uri(kind: str, index: int) -> str:
    # Stable pseudo-UUID: the loader only needs a unique, parseable URI tail.
    return f"{_BASE}/{kind}/{index:08x}-0000-4000-8000-{index:012x}"


def _alt_labels(rng: random.Random, label: str, pool: list[str]) -> str:
    """Newline-joined alt labels, the same encoding the real xlsx uses."""
    count = rng.choice([0, 1, 2, 2, 3, 3, 4, 5, 6])
    if not count:
        return ""
    variants = {f"{label} {rng.choice(pool)}" for _ in range(count)}
    if rng.random() < 0.35:
        variants.add(f"{rng.choice(pool)} {label}")
    return "\n".join(sorted(variants))


def _unique_labels(rng: random.Random, make: Any, needed: int) -> list[str]:
    """Draw distinct labels, disambiguating with a sector once combos run out."""
    seen: dict[str, None] = {}
    guard = 0
    while len(seen) < needed and guard < needed * 40:
        guard += 1
        seen.setdefault(make(), None)
    # Deterministic fill if the vocabulary could not cover the target.
    index = 0
    while len(seen) < needed:
        seen.setdefault(f"{make()} grade {index}", None)
        index += 1
    return list(seen)[:needed]


def build_document(seed: int = 20260823) -> dict[str, Any]:
    """Return an ESCO-shaped document ready for ``normalize_document``."""
    rng = random.Random(seed)

    isco_groups = []
    isco_codes: list[str] = []
    for i in range(N_ISCO_GROUPS):
        # Codes stay strings with leading zeros — ARCHITECTURE identity rule.
        code = f"{i:04d}"
        isco_codes.append(code)
        isco_groups.append(
            {
                "conceptUri": f"{_BASE}/isco/C{code}",
                "preferredLabel": f"{rng.choice(_DOMAINS)} {rng.choice(_GROUP_HEADS)} {code}",
                "code": code,
                "description": "",
            }
        )

    occ_labels = _unique_labels(
        rng,
        lambda: (
            f"{rng.choice(_SECTORS)} {rng.choice(_DOMAINS)} {rng.choice(_ROLES)}"
            if rng.random() < 0.6
            else f"{rng.choice(_DOMAINS)} {rng.choice(_ROLES)}"
        ),
        N_OCCUPATIONS,
    )
    occupations = []
    for i, label in enumerate(occ_labels):
        occupations.append(
            {
                "conceptUri": _uri("occupation", i),
                "preferredLabel": label,
                "altLabels": _alt_labels(rng, label, _SECTORS),
                "iscoGroup": rng.choice(isco_codes),
                "code": f"{rng.randrange(1000, 9999)}.{rng.randrange(1, 9)}",
                "description": "",
            }
        )

    skill_labels = _unique_labels(
        rng,
        lambda: f"{rng.choice(_VERBS)} {rng.choice(_OBJECTS)}",
        N_SKILLS,
    )
    skills = []
    for i, label in enumerate(skill_labels):
        skills.append(
            {
                "conceptUri": _uri("skill", i),
                "preferredLabel": label,
                "altLabels": _alt_labels(rng, label, _DOMAINS),
                "skillType": rng.choice(["skill/competence", "knowledge"]),
                "reuseLevel": rng.choice(["transversal", "cross-sector", "sector-specific"]),
                "description": "",
            }
        )

    group_labels = _unique_labels(
        rng,
        lambda: f"{rng.choice(_DOMAINS)} {rng.choice(_GROUP_HEADS)}",
        N_SKILL_GROUPS,
    )
    skill_groups = [
        {
            "conceptUri": _uri("skill-group", i),
            "preferredLabel": label,
            "altLabels": "",
            "code": f"S{i:04d}",
            "description": "",
        }
        for i, label in enumerate(group_labels)
    ]

    occ_uris = [o["conceptUri"] for o in occupations]
    skill_uris = [s["conceptUri"] for s in skills]
    group_uris = [g["conceptUri"] for g in skill_groups]

    # HAS_SKILL: every occupation gets at least one edge, because validate_load
    # rejects a full-size load with skill-less occupations.
    pairs: set[tuple[str, str]] = set()
    for occ in occ_uris:
        pairs.add((occ, rng.choice(skill_uris)))
    while len(pairs) < N_HAS_SKILL:
        pairs.add((rng.choice(occ_uris), rng.choice(skill_uris)))
    has_skill = [
        {
            "occupationUri": occ,
            "skillUri": skill,
            "relationType": "essential" if rng.random() < 0.45 else "optional",
        }
        for occ, skill in sorted(pairs)
    ]

    broader_occ = []
    parents = occ_uris[: max(1, N_OCCUPATIONS // 12)]
    seen_broader: set[tuple[str, str]] = set()
    while len(seen_broader) < N_BROADER_OCC:
        child = rng.choice(occ_uris)
        parent = rng.choice(parents)
        if child != parent:
            seen_broader.add((child, parent))
    broader_occ = [{"conceptUri": c, "broaderUri": p} for c, p in sorted(seen_broader)]

    seen_skill_broader: set[tuple[str, str]] = set()
    while len(seen_skill_broader) < N_BROADER_SKILL:
        child = rng.choice(skill_uris)
        parent = rng.choice(group_uris)
        seen_skill_broader.add((child, parent))
    broader_skill = [{"conceptUri": c, "broaderUri": p} for c, p in sorted(seen_skill_broader)]

    seen_related: set[tuple[str, str]] = set()
    while len(seen_related) < N_RELATED_TO:
        a, b = rng.choice(skill_uris), rng.choice(skill_uris)
        if a != b:
            seen_related.add((a, b))
    related = [
        {"originalSkillUri": a, "relatedSkillUri": b, "relationType": "optional"}
        for a, b in sorted(seen_related)
    ]

    return {
        "meta": {"suite": "esco", "mode": "synthetic-bench", "seed": seed},
        "isco_groups": isco_groups,
        "occupations": occupations,
        "skills": skills,
        "skill_groups": skill_groups,
        "occupation_skill_relations": has_skill,
        "broader_occ": broader_occ,
        "broader_skill": broader_skill,
        "skill_skill_relations": related,
    }
