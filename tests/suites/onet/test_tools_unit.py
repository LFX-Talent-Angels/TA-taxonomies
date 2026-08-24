"""Unit tests for Locate retrieval, bounded traversal, and scoring (no Neo4j)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ta_taxonomies.contract.models import Edge, Path, PolicyRef
from ta_taxonomies.suites.onet.config import (
    CONF_EXACT_CODE,
    KIND_ALIASES,
    LABEL_OCCUPATION,
    MAX_BRANCHING_PER_REL,
    MAX_PATH_DEPTH,
    MAX_PATHS,
    POLICY_BOTTLENECK,
    POLICY_LOWER_CI,
    POLICY_MEAN,
    SEARCHABLE_LABELS,
    UNWEIGHTED_EDGE_SCORE,
)
from ta_taxonomies.suites.onet.tools import (
    OnetSuite,
    _cap_per_rel_type,
    _code_cypher,
    _exact_pref_cypher,
    _lucene_infix,
    _lucene_phrase,
)


class _Result(list[dict[str, Any]]):
    def single(self) -> dict[str, Any] | None:
        return self[0] if self else None


class _Session:
    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def run(self, query: str, **_parameters: Any) -> _Result:
        self.queries.append(query)
        return _Result(self.responses.pop(0) if self.responses else [])

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _suite(session: _Session) -> OnetSuite:
    suite = object.__new__(OnetSuite)
    suite._session = lambda: session  # type: ignore[method-assign]
    return suite


def _node(node_id: str, label: str, code: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "pref_label": label,
        "source": "onet",
        "source_id": code,
        "code": code,
        "kind": LABEL_OCCUPATION,
        "labels": ["OnetNode", LABEL_OCCUPATION, "Occupation"],
    }


# -- Locate -----------------------------------------------------------------


def test_a_code_query_resolves_by_identity_before_any_text_tier() -> None:
    # "15-1252.00" is an identifier, so it must not fall through to a substring
    # search that also matches whatever else contains those digits.
    hit = _node("onet:occupation:15-1252.00", "Software Developers", "15-1252.00")
    session = _Session([[{"node": hit}]])

    result = _suite(session).search_nodes("15-1252.00")

    assert [c.method for c in result.candidates] == ["exact_code"]
    assert result.candidates[0].confidence == CONF_EXACT_CODE
    assert len(session.queries) == 1


_NO_ALIAS_HITS = [{"alt_total": 0, "alt_top": [], "cf_total": 0, "cf_top": []}]


def test_a_plain_title_never_takes_the_code_tier() -> None:
    session = _Session([[], _NO_ALIAS_HITS, [{"total": 0, "top": []}]])

    _suite(session).search_nodes("Software Developers")

    assert all("n.code = $q" not in q for q in session.queries)


def test_search_matches_concrete_labels_so_the_planner_can_seek() -> None:
    # Matching the umbrella label and filtering with labels(n) leaves the
    # planner no route to the per-label index; that regression is what PR #9
    # had to undo for ESCO.
    session = _Session([[]])

    _suite(session).search_nodes("15-1252.00")

    assert f"MATCH (n:{LABEL_OCCUPATION})" in session.queries[0]
    assert "MATCH (n:OnetNode)" not in session.queries[0]


@pytest.mark.parametrize("builder", [_exact_pref_cypher, _code_cypher])
def test_label_interpolation_refuses_anything_outside_the_closed_set(
    builder: Callable[[list[str]], str],
) -> None:
    # Cypher cannot parameterise a label, so these two builders interpolate one
    # into the query string. The closed set is the only thing between a
    # caller-supplied label and that interpolation, and `kind` reaching them
    # through KIND_ALIASES is a fact about today's call path, not a guarantee
    # about tomorrow's. Both guards could be deleted with the whole suite still
    # green before this existed.
    assert builder([LABEL_OCCUPATION])
    with pytest.raises(ValueError, match="not searchable"):
        builder(["Occupation) DETACH DELETE (n"])
    with pytest.raises(ValueError, match="not searchable"):
        # A real label from another suite is still not one of ours.
        builder(["EscoNode"])


def test_every_kind_alias_maps_into_the_searchable_set() -> None:
    # The invariant that lets search_nodes hand `kind` straight to the query
    # builders. Adding an alias pointing at a label outside the set would make
    # a documented `kind` value raise at query time instead of returning a
    # result — caught here rather than by a user typing it.
    for alias, label in KIND_ALIASES.items():
        assert label in SEARCHABLE_LABELS, f"alias {alias!r} maps outside the searchable set"


def test_unknown_kind_is_reported_rather_than_used_as_a_label() -> None:
    session = _Session([])

    result = _suite(session).search_nodes("anything", kind="Occupations; MATCH (n) DELETE n")

    assert result.warnings == ["unknown_kind:Occupations; MATCH (n) DELETE n"]
    assert session.queries == []


def test_search_reports_what_it_truncated() -> None:
    rows = [
        _node(f"onet:occupation:15-{1000 + i}.00", "x" * (i + 1), f"15-{1000 + i}.00")
        for i in range(30)
    ]
    session = _Session([[], _NO_ALIAS_HITS, [{"total": 30, "top": rows[:25]}]])

    result = _suite(session).search_nodes("data science")

    assert result.meta["matches"] == 30
    assert result.pruning is not None
    assert (result.pruning.returned, result.pruning.pruned) == (25, 5)
    assert "truncated" in result.warnings


def test_every_search_result_names_the_policy_behind_its_confidence() -> None:
    # A bare float cannot say which scale it is on, and ESCO's numbers are on a
    # different one. The policy name is what stops a caller merging them.
    hit = _node("onet:occupation:15-1252.00", "Software Developers", "15-1252.00")
    session = _Session([[{"node": hit}]])

    result = _suite(session).search_nodes("15-1252.00")

    assert result.meta["confidence_policy"] == {"name": "onet-locate-confidence", "version": "1"}


def test_lucene_helpers_decline_queries_the_index_cannot_serve() -> None:
    assert _lucene_phrase("+++") is None
    assert _lucene_phrase('say "hi"') == '"say \\"hi\\""'
    # Short terms expand over most of the term dictionary, so those queries
    # take the scan path rather than a slower index.
    assert _lucene_infix("C++") is None
    assert _lucene_infix("data science") == "+*data* +*science*"


# -- Connect ----------------------------------------------------------------


def test_unknown_relationship_types_are_refused() -> None:
    session = _Session([])

    result = _suite(session).get_neighbors("onet:occupation:15-1252.00", ["REQUIRES_SKILL"])

    assert result.warnings == ["unknown_rel_types:['REQUIRES_SKILL']"]


# -- Pathfind ---------------------------------------------------------------


def test_traversal_limits_are_rejected_before_any_query() -> None:
    for depth, paths, expected in (
        (0, 20, "invalid_max_depth"),
        (MAX_PATH_DEPTH + 1, 20, "invalid_max_depth"),
        (2, 0, "invalid_max_paths"),
        (2, MAX_PATHS + 1, "invalid_max_paths"),
    ):
        session = _Session([])
        result = _suite(session).enumerate_paths("a", "b", max_depth=depth, max_paths=paths)
        assert result.warnings == [expected]
        assert session.queries == []


def test_branching_cap_applies_per_relationship_type() -> None:
    # A single cap ordered by Importance would spend the whole budget on
    # HAS_SKILL and never expand an unrated edge, so occupation-to-occupation
    # routes would be unreachable while `pruned` looked healthy.
    rows = [{"rel_type": "HAS_SKILL", "neighbor_id": f"s{i}"} for i in range(40)]
    rows += [{"rel_type": "RELATED_TO", "neighbor_id": f"o{i}"} for i in range(5)]

    kept, cut = _cap_per_rel_type(rows)

    assert sum(1 for r in kept if r["rel_type"] == "HAS_SKILL") == MAX_BRANCHING_PER_REL
    assert sum(1 for r in kept if r["rel_type"] == "RELATED_TO") == 5
    assert cut == 40 - MAX_BRANCHING_PER_REL


# -- Evaluate ---------------------------------------------------------------


def _weighted_path(*importances: float, suppress: bool = False) -> Path:
    node_ids = [f"n{i}" for i in range(len(importances) + 1)]
    edges = [
        Edge(
            type="HAS_SKILL",
            from_id=node_ids[i],
            to_id=node_ids[i + 1],
            properties={
                "importance": value,
                "importance_lower_ci": value - 0.5,
                "recommend_suppress": suppress,
            },
        )
        for i, value in enumerate(importances)
    ]
    return Path(node_ids=node_ids, edges=edges)


def test_an_unknown_policy_is_refused_not_approximated() -> None:
    result = _suite(_Session([])).score_paths(
        [_weighted_path(4.0)], PolicyRef(name="vibes", version="1")
    )

    assert result.scored_paths == []
    assert result.warnings[0] == "unknown_policy:vibes"


def test_a_known_policy_at_an_unknown_version_is_refused() -> None:
    result = _suite(_Session([])).score_paths(
        [_weighted_path(4.0)], PolicyRef(name=POLICY_MEAN.name, version="99")
    )

    assert result.scored_paths == []
    assert result.warnings[0].startswith("unknown_policy_version:")


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        # Importance 5.0 and 3.0 normalise to 1.0 and 0.5 on the published 1–5
        # scale. Bottleneck takes the weakest hop, mean takes the average.
        (POLICY_BOTTLENECK, 0.5),
        (POLICY_MEAN, 0.75),
    ],
)
def test_policies_combine_the_same_edges_differently(policy: PolicyRef, expected: float) -> None:
    result = _suite(_Session([])).score_paths([_weighted_path(5.0, 3.0)], policy)

    assert result.scored_paths[0].score == pytest.approx(expected)
    assert result.scored_paths[0].policy == policy


def test_lower_ci_policy_penalises_a_wide_interval() -> None:
    # The whole point of scoring O*NET rather than ESCO: a rating from a small
    # sample loses to a confident one without anyone weighting sample size.
    confident = Path(
        node_ids=["a", "b"],
        edges=[
            Edge(
                type="HAS_SKILL",
                from_id="a",
                to_id="b",
                properties={"importance": 4.0, "importance_lower_ci": 3.9},
            )
        ],
    )
    shaky = Path(
        node_ids=["a", "c"],
        edges=[
            Edge(
                type="HAS_SKILL",
                from_id="a",
                to_id="c",
                properties={"importance": 4.0, "importance_lower_ci": 2.4},
            )
        ],
    )

    point = _suite(_Session([])).score_paths([shaky, confident], POLICY_BOTTLENECK)
    bounded = _suite(_Session([])).score_paths([shaky, confident], POLICY_LOWER_CI)

    assert point.scored_paths[0].score == point.scored_paths[1].score
    assert bounded.scored_paths[0].path.node_ids == ["a", "b"]


def test_a_suppressed_rating_is_neutralised_never_dropped() -> None:
    # Removing the hop would shorten the evidence and, under a minimum, raise
    # the score — rewarding a path for having unreliable data.
    suite = _suite(_Session([]))
    suppressed = suite.score_paths([_weighted_path(5.0, 1.0, suppress=True)], POLICY_BOTTLENECK)
    clean = suite.score_paths([_weighted_path(5.0, 1.0)], POLICY_BOTTLENECK)

    assert suppressed.scored_paths[0].score == UNWEIGHTED_EDGE_SCORE
    assert clean.scored_paths[0].score == 0.0
    assert "neutralised_suppressed_edges:2" in suppressed.warnings


def test_an_unrated_hop_costs_the_declared_neutral_value() -> None:
    structural = Path(
        node_ids=["a", "b"],
        edges=[Edge(type="RELATED_TO", from_id="a", to_id="b", properties={})],
    )

    result = _suite(_Session([])).score_paths([structural], POLICY_MEAN)

    assert result.scored_paths[0].score == UNWEIGHTED_EDGE_SCORE
    assert "unweighted_hops:1" in result.warnings


def test_ranking_is_deterministic_for_equal_scores() -> None:
    first = Path(
        node_ids=["a", "z"], edges=[Edge(type="RELATED_TO", from_id="a", to_id="z", properties={})]
    )
    second = Path(
        node_ids=["a", "b"], edges=[Edge(type="RELATED_TO", from_id="a", to_id="b", properties={})]
    )

    result = _suite(_Session([])).score_paths([first, second], POLICY_MEAN)

    assert [p.path.node_ids for p in result.scored_paths] == [["a", "b"], ["a", "z"]]
