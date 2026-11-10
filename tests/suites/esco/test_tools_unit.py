"""Unit tests for deterministic search and bounded path traversal."""

from __future__ import annotations

from typing import Any

from ta_taxonomies.contract.models import Path, PolicyRef
from ta_taxonomies.suites.esco.config import (
    CONF_CONTAINS,
    MAX_BRANCHING,
    MAX_PATH_DEPTH,
    MAX_PATHS,
)
from ta_taxonomies.suites.esco.tools import EscoSuite


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


def _suite_with_session(session: _Session) -> EscoSuite:
    suite = object.__new__(EscoSuite)
    suite._session = lambda: session  # type: ignore[method-assign]
    return suite


def _node(node_id: str, label: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "pref_label": label,
        "source": "esco",
        "source_id": f"https://example.test/{node_id}",
        "kind": "Occupation",
        "labels": ["EscoNode", "Occupation"],
    }


def test_contains_results_use_deterministic_order_and_equal_confidence() -> None:
    # Three round trips now, not four: the exact-alias and case-insensitive
    # tiers share one candidate pool.
    session = _Session(
        [
            [],  # 1) exact preferred label (concrete-label index seek)
            [{"alt_total": 0, "alt_top": [], "cf_total": 0, "cf_top": []}],  # 2+3)
            [
                {
                    "total": 2,
                    "top": [
                        _node("esco:occupation:b", "data scientist"),
                        _node("esco:occupation:a", "scientist"),
                    ],
                }
            ],  # 4) substring
        ]
    )
    suite = _suite_with_session(session)

    result = suite.search_nodes("scien", kind="occupation")

    assert [candidate.confidence for candidate in result.candidates] == [
        CONF_CONTAINS,
        CONF_CONTAINS,
    ]
    assert "ORDER BY alias_only, size(n.pref_label), n.id" in session.queries[-1]


def test_exact_pref_tier_never_scans_the_umbrella_label() -> None:
    # The regression this whole change exists for: matching :EscoNode and
    # filtering labels(n) cannot reach the per-label pref_label index.
    session = _Session([[{"node": _node("esco:occupation:a", "data scientist")}]])
    suite = _suite_with_session(session)

    result = suite.search_nodes("data scientist", kind="occupation")

    assert [candidate.method for candidate in result.candidates] == ["exact_pref"]
    assert "MATCH (n:Occupation)" in session.queries[0]
    assert "any(x IN labels(n)" not in session.queries[0]


def test_enumerate_paths_rejects_unbounded_requests_without_database_access() -> None:
    suite = object.__new__(EscoSuite)

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
                    "rel_type": "RELATED_TO",
                    "rel_props": {},
                    "from_id": "a",
                    "to_id": "b",
                }
            ],
            [
                {
                    "current_id": "b",
                    "neighbor_id": "a",
                    "rel_type": "RELATED_TO",
                    "rel_props": {},
                    "from_id": "a",
                    "to_id": "b",
                },
                {
                    "current_id": "b",
                    "neighbor_id": "c",
                    "rel_type": "RELATED_TO",
                    "rel_props": {},
                    "from_id": "b",
                    "to_id": "c",
                },
            ],
        ]
    )

    paths, pruned = EscoSuite._bounded_paths(
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
                {"type": "RELATED_TO", "from_id": "a", "to_id": "b"},
                {"type": "RELATED_TO", "from_id": "b", "to_id": "c"},
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
            "rel_type": "RELATED_TO",
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
                    "rel_type": "RELATED_TO",
                    "rel_props": {},
                    "from_id": "start",
                    "to_id": side,
                }
                for side in ("left", "right")
            ],
            [
                {
                    "current_id": side,
                    "neighbor_id": "hub",
                    "rel_type": "RELATED_TO",
                    "rel_props": {},
                    "from_id": side,
                    "to_id": "hub",
                }
                for side in ("left", "right")
            ],
            shared_rows,
        ]
    )

    paths, pruned = EscoSuite._bounded_paths(
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
    suite = object.__new__(EscoSuite)
    path = Path(node_ids=["a"])
    policy = PolicyRef(name="esco-essential-first", version="1")

    result = suite.score_paths([path], policy)

    assert result.paths == [path]
    assert result.meta["policy"] == {"name": "esco-essential-first", "version": "1"}


def _alias_only_session(alias_hit: dict[str, Any], *vector: dict[str, Any]) -> _Session:
    return _Session(
        [
            [],  # 1) exact preferred label
            [{"alt_total": 1, "alt_top": [alias_hit], "cf_total": 0, "cf_top": []}],
            [{"total": 1, "top": [alias_hit]}],  # 4) contains finds the same node
            [{"node": row} for row in vector],  # 5) meaning search, second opinion
        ]
    )


def test_acronym_alias_the_meaning_search_disagrees_with_is_offered_not_trusted(
    monkeypatch: Any,
) -> None:
    from ta_taxonomies.suites.esco import tools

    monkeypatch.setattr(tools, "_embed_query", lambda _q: [0.0])
    vet = _node("esco:occupation:vet", "animal artificial insemination technician")
    ai = _node("esco:occupation:ai", "artificial intelligence engineer")
    suite = _suite_with_session(_alias_only_session(vet, ai))

    result = suite.search_nodes("AI engineer", kind="occupation")

    assert [c.node.label for c in result.candidates] == [vet["pref_label"], ai["pref_label"]]
    assert [c.method for c in result.candidates] == ["exact_alt", "hybrid_rrf"]
    assert {"alias_unconfirmed", "ambiguous"} <= set(result.warnings)


def test_acronym_alias_the_meaning_search_confirms_is_kept(monkeypatch: Any) -> None:
    from ta_taxonomies.suites.esco import tools

    monkeypatch.setattr(tools, "_embed_query", lambda _q: [0.0])
    hr = _node("esco:occupation:hr", "human resources manager")
    suite = _suite_with_session(_alias_only_session(hr, hr))

    result = suite.search_nodes("HR manager", kind="occupation")

    assert [c.method for c in result.candidates] == ["exact_alt"]
    assert "alias_unconfirmed" not in result.warnings


def test_plain_word_alias_needs_no_second_opinion(monkeypatch: Any) -> None:
    from ta_taxonomies.suites.esco import tools

    def no_embedding(_q: str) -> list[float]:
        raise AssertionError("no meaning search for a non-acronym alias")

    monkeypatch.setattr(tools, "_embed_query", no_embedding)
    nanny = _node("esco:occupation:nanny", "nanny")
    session = _alias_only_session(nanny)
    session.responses.pop()  # no fifth round trip expected

    result = _suite_with_session(session).search_nodes("nurse", kind="occupation")

    assert [c.method for c in result.candidates] == ["exact_alt"]
