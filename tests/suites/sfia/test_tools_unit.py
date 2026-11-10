"""Unit tests for deterministic search and bounded path traversal."""

from __future__ import annotations

import re
from typing import Any, get_args

import pytest

from ta_taxonomies.contract.models import Path, PolicyRef, SuiteName
from ta_taxonomies.contract.protocols import Suite
from ta_taxonomies.contract.schema import SuiteSchema
from ta_taxonomies.suites._locate import Confidences, Locator
from ta_taxonomies.suites.sfia.config import (
    CONF_CONTAINS,
    CONF_EXACT_PREF,
    MAX_BRANCHING,
    MAX_PATH_DEPTH,
    MAX_PATHS,
)
from ta_taxonomies.suites.sfia.tools import SFIA_LOCATE, SfiaSuite


class _Result(list[dict[str, Any]]):
    def single(self) -> dict[str, Any] | None:
        return self[0] if self else None


class _Session:
    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.queries: list[str] = []
        self.params: list[dict[str, Any]] = []

    def run(self, query: str, **parameters: Any) -> _Result:
        self.queries.append(query)
        self.params.append(parameters)
        return _Result(self.responses.pop(0))

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _suite_with_session(session: _Session) -> SfiaSuite:
    suite = object.__new__(SfiaSuite)
    suite._session = lambda: session  # type: ignore[method-assign]
    return suite


def _node(node_id: str, label: str, kind: str = "Skill", **extra: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "pref_label": label,
        "source": "sfia",
        "source_id": node_id.rsplit(":", 1)[-1],
        "kind": kind,
        "labels": ["SfiaNode", kind],
        **extra,
    }


# -- the shared rules (mirrors tests/contract offline checks; SFIA has no occupations) --


def test_locate_config_follows_the_shared_rules() -> None:
    assert SFIA_LOCATE.source in get_args(SuiteName)
    assert SFIA_LOCATE.source == "sfia"
    assert SFIA_LOCATE.confidences == Confidences()
    assert SFIA_LOCATE.vector_index == "sfia_label_embedding"
    assert set(SFIA_LOCATE.default_labels) <= SFIA_LOCATE.searchable_labels
    for label in [*SFIA_LOCATE.searchable_labels, SFIA_LOCATE.node_label]:
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", label), label
    assert SFIA_LOCATE.groups is None


def test_kinds_resolve_and_occupation_is_not_one() -> None:
    locator = Locator(SFIA_LOCATE, session=lambda: None, embed=lambda _q: None)  # type: ignore[arg-type,return-value]
    assert locator.labels_for("skill") == ["Skill"]
    assert locator.labels_for("level") == ["SkillLevel"]
    assert locator.labels_for("skill level") == ["SkillLevel"]
    assert locator.labels_for("categories") == ["Category"]
    assert locator.labels_for("subcategory") == ["Subcategory"]
    assert locator.labels_for("occupation") is None


def test_suite_offers_every_tool() -> None:
    suite = object.__new__(SfiaSuite)
    assert isinstance(suite, Suite)
    assert isinstance(suite.suite_schema, SuiteSchema)
    assert suite.suite_schema.skill_rel_types == ()


def test_bad_input_is_answered_without_the_database() -> None:
    suite = object.__new__(SfiaSuite)
    suite._session = lambda: pytest.fail("no query for bad input")  # type: ignore[method-assign]
    assert suite.search_nodes("   ").warnings == ["empty_query"]
    assert suite.search_nodes("PROG", kind="occupation").warnings == ["unknown_kind:occupation"]
    assert suite.search_group("PROG", "x' OR 1=1").warnings == ["unknown_group:x' OR 1=1"]
    assert suite.get_neighbors("sfia:skill:PROG", ["HAS_SKILL"]).warnings == [
        "unknown_rel_types:['HAS_SKILL']"
    ]


# -- search ---------------------------------------------------------------------


def test_skill_code_resolves_to_its_skill() -> None:
    prog = _node("sfia:skill:PROG", "Programming/software development", code="PROG")
    session = _Session([[{"node": prog}]])  # 0) code tier answers on its own

    result = _suite_with_session(session).search_nodes("PROG")

    assert [c.node.id for c in result.candidates] == ["sfia:skill:PROG"]
    assert [c.method for c in result.candidates] == ["exact_code"]
    assert result.candidates[0].confidence == CONF_EXACT_PREF
    assert result.evidence == ["sfia:search:exact_code:PROG"]
    assert session.params[0]["codes"] == ["PROG"]
    assert "SkillLevel" not in session.params[0]["labels"]


def test_level_code_searches_levels() -> None:
    level = _node(
        "sfia:level:PROG:4",
        "Programming/software development (level 4)",
        kind="SkillLevel",
        code="PROG-4",
        level=4,
    )
    session = _Session([[{"node": level}]])

    result = _suite_with_session(session).search_nodes("PROG-4")

    assert [c.node.id for c in result.candidates] == ["sfia:level:PROG:4"]
    assert session.params[0]["labels"] == ["SkillLevel"]
    assert result.candidates[0].node.properties["level"] == 4


def test_lowercase_word_is_text_not_a_code() -> None:
    # "test" is a word to look for; only "TEST" names the skill.
    session = _Session(
        [
            [],  # 1) exact preferred label
            [{"alt_total": 0, "alt_top": [], "cf_total": 0, "cf_top": []}],  # 2+3)
            [{"total": 1, "top": [_node("sfia:skill:TEST", "Functional testing")]}],  # 4)
        ]
    )

    result = _suite_with_session(session).search_nodes("test")

    assert [c.method for c in result.candidates] == ["contains"]
    assert all("n.code IN $codes" not in q for q in session.queries)


def test_contains_results_use_deterministic_order_and_equal_confidence() -> None:
    session = _Session(
        [
            [],  # 1) exact preferred label (concrete-label index seek)
            [{"alt_total": 0, "alt_top": [], "cf_total": 0, "cf_top": []}],  # 2+3)
            [
                {
                    "total": 2,
                    "top": [
                        _node("sfia:skill:IRMG", "Information management"),
                        _node("sfia:skill:ISCO", "Information systems coordination"),
                    ],
                }
            ],  # 4) word-start
        ]
    )
    suite = _suite_with_session(session)

    result = suite.search_nodes("information", kind="skill")

    assert [candidate.confidence for candidate in result.candidates] == [
        CONF_CONTAINS,
        CONF_CONTAINS,
    ]
    assert "ambiguous" in result.warnings
    assert "ORDER BY alias_only, size(n.pref_label), n.id" in session.queries[-1]
    # No aliases in SFIA: the alias predicates must tolerate a missing property.
    assert "coalesce(n.alt_labels, [])" in session.queries[-1]


def test_exact_pref_tier_never_scans_the_umbrella_label() -> None:
    session = _Session([[{"node": _node("sfia:skill:TEST", "Functional testing")}]])
    suite = _suite_with_session(session)

    result = suite.search_nodes("Functional testing", kind="skill")

    assert [candidate.method for candidate in result.candidates] == ["exact_pref"]
    assert "MATCH (n:Skill)" in session.queries[0]
    assert "any(x IN labels(n)" not in session.queries[0]


def test_default_search_leaves_levels_out() -> None:
    session = _Session([[{"node": _node("sfia:skill:TEST", "Functional testing")}]])

    _suite_with_session(session).search_nodes("Functional testing")

    assert "MATCH (n:SkillLevel)" not in session.queries[0]
    assert "MATCH (n:Category)" in session.queries[0]


# -- traversal ------------------------------------------------------------------


def test_enumerate_paths_rejects_unbounded_requests_without_database_access() -> None:
    suite = object.__new__(SfiaSuite)

    assert suite.enumerate_paths("a", "b", max_depth=MAX_PATH_DEPTH + 1).warnings == [
        "invalid_max_depth"
    ]
    assert suite.enumerate_paths("a", "b", max_paths=MAX_PATHS + 1).warnings == [
        "invalid_max_paths"
    ]


def test_bounded_paths_are_cycle_free_typed_and_report_pruning() -> None:
    session = _Session(
        [
            [
                {
                    "current_id": "a",
                    "neighbor_id": "b",
                    "rel_type": "BROADER_THAN",
                    "rel_props": {},
                    "from_id": "a",
                    "to_id": "b",
                }
            ],
            [
                {
                    "current_id": "b",
                    "neighbor_id": "a",
                    "rel_type": "BROADER_THAN",
                    "rel_props": {},
                    "from_id": "a",
                    "to_id": "b",
                },
                {
                    "current_id": "b",
                    "neighbor_id": "c",
                    "rel_type": "BROADER_THAN",
                    "rel_props": {},
                    "from_id": "b",
                    "to_id": "c",
                },
            ],
        ]
    )

    paths, pruned = SfiaSuite._bounded_paths(
        session,  # type: ignore[arg-type]
        "a",
        "c",
        max_depth=2,
        max_paths=5,
    )

    assert paths == [
        Path(
            node_ids=["a", "b", "c"],
            edges=[
                {"type": "BROADER_THAN", "from_id": "a", "to_id": "b"},
                {"type": "BROADER_THAN", "from_id": "b", "to_id": "c"},
            ],
        )
    ]
    assert pruned == 1
    assert all("[*" not in query for query in session.queries)


def test_branch_pruning_counts_each_path_prefix_cut() -> None:
    shared_rows = [
        {
            "current_id": "hub",
            "neighbor_id": "target" if index == 0 else f"neighbor-{index}",
            "rel_type": "HAS_LEVEL",
            "rel_props": {},
            "from_id": "hub",
            "to_id": "target" if index == 0 else f"neighbor-{index}",
        }
        for index in range(MAX_BRANCHING + 2)
    ]
    session = _Session(
        [
            [
                {
                    "current_id": "start",
                    "neighbor_id": side,
                    "rel_type": "BROADER_THAN",
                    "rel_props": {},
                    "from_id": side,
                    "to_id": "start",
                }
                for side in ("left", "right")
            ],
            [
                {
                    "current_id": side,
                    "neighbor_id": "hub",
                    "rel_type": "BROADER_THAN",
                    "rel_props": {},
                    "from_id": "hub",
                    "to_id": side,
                }
                for side in ("left", "right")
            ],
            shared_rows,
        ]
    )

    paths, pruned = SfiaSuite._bounded_paths(
        session,  # type: ignore[arg-type]
        "start",
        "target",
        max_depth=3,
        max_paths=10,
    )

    assert len(paths) == 2
    # Two rows over the branch cap are cut for each of the two prefixes (4),
    # then 24 retained non-target extensions per prefix hit max depth (48).
    assert pruned == 52


def test_score_paths_uses_typed_contract_even_while_policy_is_deferred() -> None:
    suite = object.__new__(SfiaSuite)
    path = Path(node_ids=["a"])
    policy = PolicyRef(name="sfia-level-distance", version="1")

    result = suite.score_paths([path], policy)

    assert result.paths == [path]
    assert result.warnings[0] == "score_paths_not_implemented"
    assert result.meta["policy"] == {"name": "sfia-level-distance", "version": "1"}
