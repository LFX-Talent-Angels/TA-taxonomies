"""Unit tests for O*NET search order, bounds, and importance scoring."""

from __future__ import annotations

from typing import Any

from ta_taxonomies.contract.models import Edge, Path, PolicyRef
from ta_taxonomies.suites.onet.config import CONF_CONTAINS, MAX_PATH_DEPTH, MAX_PATHS
from ta_taxonomies.suites.onet.tools import IMPORTANCE_POLICY, OnetSuite


class _Result(list[dict[str, Any]]):
    def single(self) -> dict[str, Any] | None:
        return self[0] if self else None


class _Session:
    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def run(self, query: str, **_parameters: Any) -> _Result:
        self.queries.append(query)
        return _Result(self.responses.pop(0))

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _suite_with_session(session: _Session) -> OnetSuite:
    suite = object.__new__(OnetSuite)
    suite._session = lambda: session  # type: ignore[method-assign]
    return suite


def _node(node_id: str, label: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "pref_label": label,
        "source": "onet",
        "source_id": node_id,
        "kind": "Occupation",
        "labels": ["OnetNode", "Occupation"],
    }


def test_contains_results_use_deterministic_order() -> None:
    session = _Session(
        [
            [],
            [{"alt_total": 0, "alt_top": [], "cf_total": 0, "cf_top": []}],
            [
                {
                    "total": 2,
                    "top": [
                        _node("onet:occupation:b", "data scientist"),
                        _node("onet:occupation:a", "scientist"),
                    ],
                }
            ],
        ]
    )
    result = _suite_with_session(session).search_nodes("scien", kind="occupation")
    assert [c.confidence for c in result.candidates] == [CONF_CONTAINS, CONF_CONTAINS]
    assert "ORDER BY size(n.pref_label), n.id" in session.queries[-1]


def test_exact_pref_matches_concrete_occupation_label() -> None:
    session = _Session([[{"node": _node("onet:occupation:a", "Software Developers")}]])
    result = _suite_with_session(session).search_nodes("Software Developers", kind="occupation")
    assert [c.method for c in result.candidates] == ["exact_pref"]
    assert "MATCH (n:Occupation)" in session.queries[0]
    assert result.candidates[0].node.source == "onet"


def test_enumerate_paths_rejects_unbounded_requests() -> None:
    suite = object.__new__(OnetSuite)
    assert suite.enumerate_paths("a", "b", max_depth=MAX_PATH_DEPTH + 1).warnings == [
        "invalid_max_depth"
    ]
    assert suite.enumerate_paths("a", "b", max_paths=MAX_PATHS + 1).warnings == [
        "invalid_max_paths"
    ]


def test_score_paths_means_importance_and_rejects_unknown_policy() -> None:
    suite = object.__new__(OnetSuite)
    path = Path(
        node_ids=["onet:occupation:a", "onet:element:x", "onet:occupation:b"],
        edges=[
            Edge(
                type="HAS_SKILL",
                from_id="onet:occupation:a",
                to_id="onet:element:x",
                properties={"importance": 4.0, "relation_type": "transferable"},
            ),
            Edge(
                type="HAS_SKILL",
                from_id="onet:element:x",
                to_id="onet:occupation:b",
                properties={"importance": 2.0},
            ),
        ],
    )
    scored = suite.score_paths([path], IMPORTANCE_POLICY)
    assert scored.scored_paths[0].score == 3.0
    assert "declared" in str(scored.meta.get("note", "")).lower()

    unknown = suite.score_paths([path], PolicyRef(name="made-up", version="1"))
    assert unknown.warnings == ["unknown_policy:made-up"]
    assert unknown.scored_paths == []
