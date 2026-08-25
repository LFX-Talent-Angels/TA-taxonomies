"""SfiaSuite pure logic: no Neo4j, no I/O.

Two kinds of test live here. The ordinary kind checks behaviour. The other kind
checks that ``config.py`` actually *governs* behaviour rather than merely
agreeing with it — a constant that is read but not enforced is worse than no
constant, because a reader draws a conclusion from it that nothing holds the
code to. Those tests monkeypatch the config and require the query to follow;
asserting the query names today's labels would not distinguish a derived list
from a literal that currently says the same thing.
"""

from __future__ import annotations

import inspect

import pytest

from ta_taxonomies.contract.models import Edge, Path, PolicyRef
from ta_taxonomies.contract.protocols import Suite
from ta_taxonomies.suites.sfia import config
from ta_taxonomies.suites.sfia.tools import (
    SfiaSuite,
    _code_cypher,
    _exact_pref_cypher,
    _lucene_infix,
    _lucene_phrase,
    _normalize_level,
)

BOTTLENECK = config.POLICY_LEVEL_BOTTLENECK
PEAK = config.POLICY_LEVEL_PEAK
MEAN = config.POLICY_LEVEL_MEAN


def _suite() -> SfiaSuite:
    """A suite whose driver is never used — these tests never open a session."""
    return SfiaSuite(driver=None)  # type: ignore[arg-type]


# -- Locate screening -------------------------------------------------------


@pytest.mark.parametrize("q", ["PROG", "prog", "ISCO", "Test"])
def test_four_letters_are_screened_as_a_possible_code(q: str) -> None:
    assert SfiaSuite._looks_like_code(q) is True


@pytest.mark.parametrize("q", ["PRO", "PROGR", "PR0G", "software design", ""])
def test_anything_else_goes_straight_to_the_name_tiers(q: str) -> None:
    assert SfiaSuite._looks_like_code(q) is False


def test_a_four_letter_word_that_is_not_a_code_is_allowed_to_fall_through() -> None:
    """The screen is syntactic; the seek decides.

    "Test" looks like a code and is also an English word. Screening it in costs
    one indexed seek that returns nothing, after which the name tiers run — which
    is the right trade against refusing to resolve codes users actually type.
    """
    assert SfiaSuite._looks_like_code("Test") is True


def test_the_code_screen_follows_the_configured_code_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ta_taxonomies.suites.sfia.tools._LOOKS_LIKE_CODE",
        __import__("re").compile(r"^[A-Za-z]{5}$"),
    )
    assert SfiaSuite._looks_like_code("PROGR") is True
    assert SfiaSuite._looks_like_code("PROG") is False


# -- Lucene helpers ---------------------------------------------------------


def test_a_phrase_query_is_quoted_and_escaped() -> None:
    assert _lucene_phrase('a "b"') == '"a \\"b\\""'


def test_a_query_with_no_indexable_terms_declines_the_index() -> None:
    """None means "scan", not "no match" — the difference matters."""
    assert _lucene_phrase("///") is None
    assert _lucene_infix("///") is None


def test_short_terms_decline_the_infix_index() -> None:
    assert _lucene_infix("ab") is None
    assert _lucene_infix("programming") == "+*programming*"


def test_the_wildcard_floor_is_read_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ta_taxonomies.suites.sfia.tools.MIN_WILDCARD_TERM", 20)
    assert _lucene_infix("programming") is None


# -- Cypher builders and the closed label set -------------------------------


def test_a_label_outside_the_searchable_set_is_refused() -> None:
    """Last line between a caller-supplied label and string interpolation.

    Cypher cannot parameterise a label, so the concrete label is interpolated.
    Today ``search_nodes`` only reaches these builders through ``KIND_ALIASES``,
    which means the guard is unreachable from the public API — a fact about the
    current call path, not a property of the code.
    """
    with pytest.raises(ValueError, match="not searchable"):
        _exact_pref_cypher(["Skill"])
    with pytest.raises(ValueError, match="not searchable"):
        _code_cypher(["MATCH (n) DETACH DELETE n //"])


def test_the_searched_labels_are_derived_from_config_not_hardcoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ta_taxonomies.suites.sfia.tools.SEARCHABLE_LABELS",
        (*config.SEARCHABLE_LABELS, "SfiaMadeUp"),
    )
    assert "SfiaMadeUp" in _exact_pref_cypher(["SfiaMadeUp"])


def test_one_seek_arm_per_concrete_label() -> None:
    cypher = _exact_pref_cypher([config.LABEL_SKILL, config.LABEL_LEVEL])
    assert cypher.count("MATCH (n:") == 2
    assert "UNION" in cypher
    # Never the umbrella: from it the planner cannot reach a per-label index.
    assert f"MATCH (n:{config.LABEL_SFIA_NODE})" not in cypher


# -- Connect ----------------------------------------------------------------


def test_an_undeclared_relationship_type_is_refused() -> None:
    result = _suite().get_neighbors("sfia:skill:PROG", rel_types=["HAS_SKILL"])
    assert result.warnings == ["unknown_rel_types:['HAS_SKILL']"]


def test_the_traversable_set_is_read_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ta_taxonomies.suites.sfia.tools.TRAVERSABLE_RELS",
        frozenset({*config.TRAVERSABLE_RELS, "HAS_SKILL"}),
    )
    # No longer rejected by the declared set, so the call gets past the guard
    # and reaches the driver this suite deliberately does not have. Reaching the
    # driver *is* the assertion: the rejection is now gone, and a hardcoded list
    # in tools.py would still have rejected it.
    with pytest.raises(AttributeError):
        _suite().get_neighbors("sfia:skill:PROG", rel_types=["HAS_SKILL"])


# -- Evaluate ---------------------------------------------------------------


def _has_level(from_id: str, level: int) -> Edge:
    return Edge(
        type=config.REL_HAS_LEVEL,
        from_id=from_id,
        to_id=f"sfia:level:{level}",
        properties={"level": level},
    )


def _path(*levels: int) -> Path:
    """A skill hopping through the given levels, back and forth."""
    node_ids = ["sfia:skill:AAAA"]
    edges = []
    for index, level in enumerate(levels):
        level_id = f"sfia:level:{level}"
        edges.append(_has_level(node_ids[-1], level))
        node_ids.append(level_id)
        if index + 1 < len(levels):
            skill = f"sfia:skill:{'BCDE'[index]}{'BCDE'[index]}{'BCDE'[index]}{'BCDE'[index]}"
            edges.append(
                Edge(
                    type=config.REL_HAS_LEVEL,
                    from_id=skill,
                    to_id=level_id,
                    properties={"level": level},
                )
            )
            node_ids.append(skill)
    return Path(node_ids=node_ids, edges=edges)


def test_levels_normalise_across_the_published_scale() -> None:
    assert _normalize_level(config.LEVEL_MIN) == 0.0
    assert _normalize_level(config.LEVEL_MAX) == 1.0
    assert _normalize_level(4) == pytest.approx(0.5)
    # No level at all is the declared neutral value, not zero: 0.0 would read as
    # "measured and found worthless".
    assert _normalize_level(None) == config.UNWEIGHTED_EDGE_SCORE


def test_an_unrated_hop_is_the_declared_neutral_value() -> None:
    edge = Edge(type=config.REL_RELATED_TO, from_id="sfia:skill:AAAA", to_id="sfia:skill:BBBB")
    assert SfiaSuite._edge_score(edge) is None


def test_a_ladder_step_is_scored_by_the_rung_it_arrives_at() -> None:
    """Walking the ladder downward must not score as an ascent.

    The edge is stored in one direction, but path expansion is undirected, so a
    route can step *down* from level 6 to level 5. Scoring that hop as if it had
    reached 6 is wrong in exactly the place the ladder exists to be right.
    """
    edge = Edge(
        type=config.REL_MAY_LEAD_TO,
        from_id="sfia:level:5",
        to_id="sfia:level:6",
        properties={"from_level": 5, "to_level": 6},
    )
    assert SfiaSuite._edge_score(edge, "sfia:level:6") == pytest.approx(_normalize_level(6))
    assert SfiaSuite._edge_score(edge, "sfia:level:5") == pytest.approx(_normalize_level(5))


def test_the_three_policies_disagree_on_the_same_paths() -> None:
    """Bottleneck, peak and mean are different questions, not tuning variants."""
    steady = _path(4, 4)
    spiky = _path(2, 7)
    suite = _suite()

    ranked = {
        policy.name: [s.score for s in suite.score_paths([steady, spiky], policy).scored_paths]
        for policy in (BOTTLENECK, PEAK, MEAN)
    }
    bottleneck_top = suite.score_paths([steady, spiky], BOTTLENECK).scored_paths[0]
    peak_top = suite.score_paths([steady, spiky], PEAK).scored_paths[0]

    assert bottleneck_top.path == steady, "the weakest rung decides"
    assert peak_top.path == spiky, "the highest rung decides"
    assert ranked[MEAN.name][0] == pytest.approx(0.5, abs=0.1)


def test_scores_are_sorted_descending_and_carry_the_policy() -> None:
    suite = _suite()
    result = suite.score_paths([_path(2), _path(6)], PEAK)
    scores = [item.score for item in result.scored_paths]
    assert scores == sorted(scores, reverse=True)
    assert all(item.policy == PEAK for item in result.scored_paths)
    assert result.evidence == [f"sfia:score:{PEAK.name}/{PEAK.version}"]


def test_an_unknown_policy_is_refused_rather_than_approximated() -> None:
    result = _suite().score_paths([_path(4)], PolicyRef(name="made-up", version="1"))
    assert result.scored_paths == []
    assert result.warnings[0] == "unknown_policy:made-up"


def test_a_known_policy_at_an_unknown_version_is_refused() -> None:
    result = _suite().score_paths([_path(4)], PolicyRef(name=PEAK.name, version="99"))
    assert result.scored_paths == []
    assert result.warnings[0].startswith("unknown_policy_version:")


def test_a_policy_in_the_config_without_a_rule_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bare-``else`` trap: a named policy silently borrowing another's rule.

    Adding a policy to SUPPORTED_POLICIES is a one-line edit; forgetting the
    branch in ``score_paths`` would return the bottleneck number under the new
    name, which is exactly what PolicyRef exists to prevent.
    """
    ghost = PolicyRef(name="sfia-level-ghost", version="1")
    monkeypatch.setitem(
        __import__(
            "ta_taxonomies.suites.sfia.tools", fromlist=["SUPPORTED_POLICIES"]
        ).SUPPORTED_POLICIES,
        ghost.name,
        ghost,
    )
    with pytest.raises(ValueError, match="no scoring rule"):
        _suite().score_paths([_path(4)], ghost)


def test_unweighted_hops_are_counted_in_the_warnings() -> None:
    """0.5 on a 1–7 scale *is* level 4, so a structural hop is not free.

    Reporting the count keeps that a declared consequence rather than a hidden
    one — a caller comparing two paths can see how much of the score was ours.
    """
    path = Path(
        node_ids=["sfia:skill:AAAA", "sfia:skill:BBBB"],
        edges=[
            Edge(type=config.REL_RELATED_TO, from_id="sfia:skill:AAAA", to_id="sfia:skill:BBBB")
        ],
    )
    result = _suite().score_paths([path], MEAN)
    assert result.warnings == ["unweighted_hops:1"]
    assert result.scored_paths[0].score == config.UNWEIGHTED_EDGE_SCORE


# -- Contract conformance ---------------------------------------------------


def test_the_suite_exposes_the_contract_method_names() -> None:
    """``isinstance`` against a runtime_checkable Protocol checks names only.

    Not signatures, not return types — an object whose four methods take no
    arguments at all satisfies it. Kept because it still catches a method going
    missing; the signature test below is what pins the rest.
    """
    assert isinstance(_suite(), Suite)


@pytest.mark.parametrize(
    "method", ["search_nodes", "get_neighbors", "enumerate_paths", "score_paths"]
)
def test_each_tool_matches_the_protocols_signature(method: str) -> None:
    """Catches a drifted default, which no behavioural test in this suite can.

    ``enumerate_paths(a, b)`` with ``max_depth`` quietly changed from 4 to 3
    would leave every other test green while TA-agents searched a hop shallower.
    """
    assert inspect.signature(getattr(SfiaSuite, method)) == inspect.signature(
        getattr(Suite, method)
    )


def test_the_suite_names_itself_with_a_contract_suite_name() -> None:
    assert SfiaSuite.name == "sfia"
