"""A group named like an occupation, or like a more specific group, is not listed twice."""

from __future__ import annotations

from typing import Any

from ta_taxonomies.suites._locate import drop_shadowed_groups, locate_result, merged_result
from ta_taxonomies.suites.bls.tools import BLS_LOCATE
from ta_taxonomies.suites.esco.tools import ESCO_LOCATE
from ta_taxonomies.suites.jobtech.tools import JOBTECH_LOCATE


def _row(node_id: str, label: str, kind: str, code: str = "") -> dict[str, Any]:
    return {"id": node_id, "pref_label": label, "labels": [kind], "kind": kind, "code": code}


NURSES = [
    _row("bls:occupation:29-1141", "Registered Nurses", "Occupation", "29-1141"),
    _row("bls:group:29-1140", "Registered Nurses", "SOCGroup", "29-1140"),
    _row("bls:occupation:29-1151", "Nurse Anesthetists", "Occupation", "29-1151"),
]


def test_a_group_named_like_an_occupation_is_dropped() -> None:
    kept = drop_shadowed_groups(BLS_LOCATE, NURSES)
    assert [r["id"] for r in kept] == ["bls:occupation:29-1141", "bls:occupation:29-1151"]


def test_the_most_specific_of_same_named_groups_is_kept() -> None:
    rows = [
        _row("jobtech:group:a", "Officerare", "SSYKGroup", "0"),
        _row("jobtech:group:b", "Officerare", "SSYKGroup", "011"),
        _row("jobtech:group:c", "Officerare", "SSYKGroup", "0110"),
    ]
    found = locate_result(JOBTECH_LOCATE, rows, 3, 0.95, "exact_pref", "x", [])
    assert [n.id for n in found.nodes] == ["jobtech:group:c"]
    assert found.meta["matches"] == 1


def test_a_lone_title_left_after_the_drop_is_not_ambiguous() -> None:
    rows = NURSES[:2]
    found = locate_result(BLS_LOCATE, rows, 2, 0.7, "contains", "x", [], ambiguous_if_many=True)
    assert "ambiguous" not in found.warnings


def test_merged_results_drop_them_too() -> None:
    found = merged_result(BLS_LOCATE, NURSES[:1], NURSES[1:], 3, [], "x")
    assert [n.id for n in found.nodes] == ["bls:occupation:29-1141", "bls:occupation:29-1151"]
    assert found.meta["matches"] == 2


def test_suites_that_do_not_opt_in_keep_every_row() -> None:
    assert drop_shadowed_groups(ESCO_LOCATE, NURSES) == NURSES
