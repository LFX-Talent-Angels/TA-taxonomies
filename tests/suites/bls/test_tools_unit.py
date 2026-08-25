"""Tool internals that do not need a graph: scoring, policy refusal, query shape."""

from __future__ import annotations

import pytest

from ta_taxonomies.contract import Edge, Path, PolicyRef
from ta_taxonomies.suites.bls import config
from ta_taxonomies.suites.bls.tools import (
    BlsSuite,
    _code_cypher,
    _exact_pref_cypher,
    _lucene_infix,
    _lucene_phrase,
    _normalize_growth,
    _normalize_share,
)


class _Driver:
    """Enough of a Driver for the methods that never touch a session."""

    def session(self, database: str | None = None) -> None:  # pragma: no cover
        raise AssertionError("this test must not open a session")


def _suite() -> BlsSuite:
    return BlsSuite(_Driver())  # type: ignore[arg-type]


def _path(*edges: Edge) -> Path:
    node_ids = [edges[0].from_id, *(e.to_id for e in edges)]
    return Path(node_ids=node_ids, edges=list(edges))


def _employed_in(
    share: float | None = None,
    growth: float | None = None,
    *,
    occupation: str = "bls:occupation:15-1252",
    industry: str = "bls:industry:5415A1",
) -> Edge:
    return Edge(
        type=config.REL_EMPLOYED_IN,
        from_id=occupation,
        to_id=industry,
        properties={
            "industry_share_of_occupation_base": share,
            "employment_change_percent": growth,
        },
    )


def _two_hop(first: float, second: float) -> Path:
    """occupation → industry → occupation, the shape Pathfind actually returns."""
    return Path(
        node_ids=["bls:occupation:15-1252", "bls:industry:5415A1", "bls:occupation:15-1211"],
        edges=[
            _employed_in(share=first),
            _employed_in(share=second, occupation="bls:occupation:15-1211"),
        ],
    )


def _broader() -> Edge:
    return Edge(
        type=config.REL_BROADER_THAN,
        from_id="bls:occupation:15-1252",
        to_id="bls:soc:15-1250",
        properties={"levels_skipped": 0},
    )


# --- normalisation ---------------------------------------------------------


def test_a_published_share_maps_onto_the_unit_interval() -> None:
    assert _normalize_share(0.0) == 0.0
    assert _normalize_share(50.0) == 0.5
    assert _normalize_share(100.0) == 1.0
    assert _normalize_share(None) is None


def test_no_change_scores_the_midpoint_and_the_window_is_reported_when_it_bites() -> None:
    """The window is a declared modelling decision, so a clamp must be visible.

    260 of the 110,353 industry cells in the 2024–34 round fall outside ±50%,
    up to +208.9%. Without the flag, a clamped 1.0 is indistinguishable from a
    measured +50%.
    """
    assert _normalize_growth(0.0) == (0.5, False)
    assert _normalize_growth(config.GROWTH_WINDOW_PCT) == (1.0, False)
    assert _normalize_growth(-config.GROWTH_WINDOW_PCT) == (0.0, False)
    assert _normalize_growth(208.9) == (1.0, True)
    assert _normalize_growth(None) is None


# --- scoring ---------------------------------------------------------------


def test_the_bottleneck_policy_is_the_weakest_hop() -> None:
    suite = _suite()
    path = _two_hop(80.0, 20.0)

    result = suite.score_paths([path], config.POLICY_SHARE_BOTTLENECK)

    assert result.scored_paths[0].score == pytest.approx(0.2)
    assert result.scored_paths[0].policy == config.POLICY_SHARE_BOTTLENECK


def test_the_mean_policy_rewards_a_route_that_is_solid_on_average() -> None:
    suite = _suite()
    path = _two_hop(80.0, 20.0)

    result = suite.score_paths([path], config.POLICY_SHARE_MEAN)

    assert result.scored_paths[0].score == pytest.approx(0.5)


def test_the_growth_policy_ranks_by_where_employment_is_heading() -> None:
    """The one no other adopted suite can produce.

    O*NET ranks by how important a skill is to a job. This ranks by whether the
    jobs will be there in ten years, which is a different question and a
    published number.
    """
    suite = _suite()
    shrinking = _path(_employed_in(share=90.0, growth=-30.0))
    growing = _path(_employed_in(share=10.0, growth=40.0))

    by_share = suite.score_paths([shrinking, growing], config.POLICY_SHARE_BOTTLENECK)
    by_growth = suite.score_paths([shrinking, growing], config.POLICY_PROJECTED_GROWTH)

    assert by_share.scored_paths[0].path == shrinking
    assert by_growth.scored_paths[0].path == growing


def test_a_structural_hop_is_neutral_and_the_count_is_reported() -> None:
    """SOC's tree is structure, not measurement.

    0.0 would read as "measured and found worthless"; anything but the neutral
    midpoint would be a claim BLS does not make about rolling up.
    """
    suite = _suite()
    path = _path(_broader())

    result = suite.score_paths([path], config.POLICY_SHARE_BOTTLENECK)

    assert result.scored_paths[0].score == config.UNWEIGHTED_EDGE_SCORE
    assert "unweighted_hops:1" in result.warnings


def test_a_clamped_growth_value_never_passes_for_a_measured_one() -> None:
    suite = _suite()
    result = suite.score_paths(
        [_path(_employed_in(share=10.0, growth=208.9))], config.POLICY_PROJECTED_GROWTH
    )
    assert "growth_clamped_to_window:1" in result.warnings


def test_a_single_node_path_scores_the_declared_neutral_value() -> None:
    suite = _suite()
    result = suite.score_paths(
        [Path(node_ids=["bls:occupation:15-1252"])], config.POLICY_SHARE_MEAN
    )
    assert result.scored_paths[0].score == config.UNWEIGHTED_EDGE_SCORE


@pytest.mark.parametrize(
    ("name", "version", "warning"),
    [
        ("bls-invented-policy", "1", "unknown_policy:bls-invented-policy"),
        ("bls-projected-growth", "99", "unknown_policy_version:bls-projected-growth:99"),
    ],
)
def test_an_undeclared_policy_is_refused_rather_than_approximated(
    name: str, version: str, warning: str
) -> None:
    """Returning a number under a name that never defined one is what PolicyRef prevents."""
    suite = _suite()
    result = suite.score_paths(
        [_path(_employed_in(share=50.0))], PolicyRef(name=name, version=version)
    )

    assert warning in result.warnings
    assert result.scored_paths == []
    # The paths come back untouched, so a caller can retry under a real policy.
    assert len(result.paths) == 1


def test_the_result_names_the_scales_it_used() -> None:
    """A bare float cannot say what it measured; meta can."""
    suite = _suite()
    result = suite.score_paths([_path(_employed_in(share=50.0))], config.POLICY_SHARE_BOTTLENECK)
    assert result.meta["share_scale"] == [config.SHARE_MIN, config.SHARE_MAX]
    assert result.meta["growth_window_pct"] == config.GROWTH_WINDOW_PCT
    assert result.meta["policy"] == config.POLICY_SHARE_BOTTLENECK.model_dump()


# --- Locate query construction ---------------------------------------------


def test_locate_seeks_concrete_labels_rather_than_filtering_the_umbrella() -> None:
    """PR #9: from the umbrella label the planner cannot reach a per-label index.

    One arm per concrete label, unioned, so every exact tier is a seek.
    """
    cypher = _exact_pref_cypher([config.LABEL_OCCUPATION, config.LABEL_SOC_GROUP])

    assert f"MATCH (n:{config.LABEL_OCCUPATION})" in cypher
    assert f"MATCH (n:{config.LABEL_SOC_GROUP})" in cypher
    assert "UNION" in cypher
    assert f"MATCH (n:{config.LABEL_BLS_NODE})" not in cypher


@pytest.mark.parametrize("builder", [_exact_pref_cypher, _code_cypher])
def test_a_label_outside_the_closed_set_never_reaches_cypher(builder: object) -> None:
    """Cypher cannot parameterise a label, so this is the last guard before
    string interpolation. Unreachable from the public API today, which is a
    fact about the current call path and not a property of the code."""
    with pytest.raises(ValueError, match="not searchable"):
        builder(["BlsOccupation; MATCH (n) DETACH DELETE n //"])  # type: ignore[operator]


def test_the_searched_label_set_follows_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserting the query names the right labels cannot tell a derived list
    from a literal that currently agrees. Only a varying config can."""
    monkeypatch.setattr(config, "SEARCHABLE_LABELS", (*config.SEARCHABLE_LABELS, "BlsInvented"))
    import ta_taxonomies.suites.bls.tools as tools

    monkeypatch.setattr(tools, "SEARCHABLE_LABELS", config.SEARCHABLE_LABELS)
    assert "MATCH (n:BlsInvented)" in _exact_pref_cypher(["BlsInvented"])


# --- Lucene helpers --------------------------------------------------------


def test_a_query_the_index_cannot_find_falls_back_to_the_scan() -> None:
    """None means "scan", not "no match" — pure punctuation indexes to nothing."""
    assert _lucene_phrase("---") is None
    assert _lucene_phrase("Registered nurses") == '"Registered nurses"'


def test_a_short_term_declines_the_wildcard_index() -> None:
    """An infix wildcard below MIN_WILDCARD_TERM expands over most of the term
    dictionary and costs more than the scan it replaces."""
    assert _lucene_infix("rn") is None
    assert _lucene_infix("nurse") == "+*nurse*"
    assert _lucene_infix("registered nurse") == "+*registered* +*nurse*"


# --- the code tier ---------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("15-1252", ("15-1252", False)),
        ("151252", ("15-1252", False)),
        ("onet:soc:15-1252", ("15-1252", True)),
        ("onet:occupation:15-1252.00", ("15-1252", True)),
        ("15-1252.00", ("15-1252", True)),
        ("Registered nurses", (None, False)),
        ("esco:occupation:f2b15a0e", (None, False)),
    ],
)
def test_the_code_tier_separates_this_suites_codes_from_borrowed_ones(
    query: str, expected: tuple[str | None, bool]
) -> None:
    """A hit from another suite's identifier is reported under its own method.

    The join is a string identity on the SOC code, not a stored crosswalk, and
    conflating the two would let a lookup pass for a citation.
    """
    assert BlsSuite._code_query(query) == expected
