"""How a broad Locate match set splits into occupation groups.

"engineer" matches about 200 ESCO titles; the first 25 by length say little
about them. The group counts (civil 11, mechanical 24, electrical ...) let the
assistant ask "which area?" with real category names instead of guessing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from ta_taxonomies.contract.models import ToolResult

#: Groups reported per result, largest first. ``group_total`` says how many exist.
GROUPS_SHOWN = 12


def top_groups(codes: Iterable[str]) -> tuple[list[tuple[str, int]], int]:
    """(code, count) pairs, largest first then by code, and the number of groups."""
    counts = Counter(code for code in codes if code)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:GROUPS_SHOWN], len(counts)


def attach_groups(
    result: ToolResult,
    shown: list[tuple[str, int]],
    total: int,
    names: Mapping[str, Mapping[str, Any]],
    scheme: str,
) -> ToolResult:
    """Add ``meta.groups`` when the matches span two groups or more.

    ``names`` maps a code to ``{"id": ..., "label": ...}``; a code without a
    name is still counted, with its code as the label.
    """
    if total < 2:
        return result
    result.meta["groups"] = [
        {
            "id": names.get(code, {}).get("id"),
            "code": code,
            "label": names.get(code, {}).get("label") or code,
            "count": count,
        }
        for code, count in shown
    ]
    result.meta["group_total"] = total
    result.meta["group_scheme"] = scheme
    return result
