"""Per-suite schema metadata — relation types and node kinds for each taxonomy.

Each taxonomy suite (EscoSuite, OnetSuite, ...) returns one SuiteSchema from its
suite_schema property. TA-agents callers read from it instead of hardcoding
taxonomy-specific strings.

Adding a new suite: implement suite_schema here in TA-taxonomies only.
TA-agents needs no changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SuiteSchema:
    """Declares what relation types and property values a suite uses per concept."""

    skill_rel_types: tuple[str, ...]
    """Relation types that represent skills in this suite.

    ESCO: ("HAS_SKILL",)
    O*NET: ("HAS_SKILL", "USES_SOFTWARE")
    """

    optional_rel_values: frozenset[str] = field(default_factory=frozenset)
    """Edge relation_type property values meaning non-essential / optional.

    ESCO: frozenset({"optional"})
    O*NET: frozenset({"optional", "transferable"})
    """

    group_rel_type: str | None = None
    """Relation type used to reach occupation-grouping nodes for the Locate picker.

    ESCO: "CLASSIFIED_UNDER"
    O*NET: None  (no graph-based grouping)
    """

    group_node_kinds: frozenset[str] = field(default_factory=frozenset)
    """Node kind values that represent occupation groups (matched case-insensitively).

    ESCO: frozenset({"ISCOGroup", "isco group"})
    O*NET: frozenset()
    """
