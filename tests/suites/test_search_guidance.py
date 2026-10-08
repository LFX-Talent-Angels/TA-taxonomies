"""Word-start contains, group counts, group narrowing and the Tier-5 keyword list."""

from __future__ import annotations

from typing import Any

import pytest

from ta_taxonomies.contract.models import ToolResult
from ta_taxonomies.suites._groups import GROUPS_SHOWN, attach_groups, top_groups
from ta_taxonomies.suites._keywords import MAX_TERMS, keyword_query
from ta_taxonomies.suites._wordstart import word_start_pattern
from ta_taxonomies.suites.esco.tools import EscoSuite
from ta_taxonomies.suites.onet.tools import OnetSuite


class _Result(list[dict[str, Any]]):
    def single(self) -> dict[str, Any] | None:
        return self[0] if self else None


class _Session:
    """Replays canned rows and records every query with its parameters."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> _Result:
        self.calls.append((query, parameters))
        return _Result(self.responses.pop(0))

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _suite(cls: type[Any], session: _Session) -> Any:
    suite = object.__new__(cls)
    suite._session = lambda: session
    return suite


def _node(node_id: str, label: str, source: str = "esco") -> dict[str, Any]:
    return {
        "id": node_id,
        "pref_label": label,
        "source": source,
        "source_id": node_id,
        "kind": "Occupation",
        "labels": ["Occupation"],
    }


_NO_EXACT: list[list[dict[str, Any]]] = [
    [],  # 1) exact preferred label
    [{"alt_total": 0, "alt_top": [], "cf_total": 0, "cf_top": []}],  # 2+3)
]


# -- word-start pattern ------------------------------------------------------


def test_word_start_pattern_checks_the_word_boundary() -> None:
    assert word_start_pattern("swe") == r"(?isu).*(?<![\p{L}\p{N}])swe.*"


def test_word_start_pattern_escapes_everything_but_letters_digits_spaces() -> None:
    assert word_start_pattern("C++ dev") == r"(?isu).*(?<![\p{L}\p{N}])C\+\+ dev.*"
    assert word_start_pattern('a"b\\c') == r"(?isu).*(?<![\p{L}\p{N}])a\"b\\c.*"


def test_word_start_pattern_skips_the_boundary_for_leading_punctuation() -> None:
    assert word_start_pattern(".net") == r"(?isu).*\.net.*"


# -- Tier-5 keyword query -----------------------------------------------------


def test_keyword_query_drops_stop_words_and_stems_to_prefixes() -> None:
    assert keyword_query("an engineer who builds buildings") == "engine* build*"


def test_keyword_query_keeps_short_stems_whole() -> None:
    # "uses" would stem to "us"; below the minimum stem it stays as it is.
    assert keyword_query("nurse uses tools") == "nurse* uses* tool*"


def test_keyword_query_is_none_without_topic_words() -> None:
    assert keyword_query("I want to be a") is None
    assert keyword_query("+++") is None


def test_keyword_query_caps_the_terms() -> None:
    words = " ".join(f"topic{i}word" for i in range(MAX_TERMS + 4))
    assert len((keyword_query(words) or "").split()) == MAX_TERMS


# -- group counts -------------------------------------------------------------


def test_top_groups_orders_by_count_then_code_and_caps() -> None:
    codes = ["2144"] * 3 + ["2142"] * 3 + ["3115"] + [str(1000 + i) for i in range(GROUPS_SHOWN)]
    shown, total = top_groups([*codes, ""])
    assert shown[:2] == [("2142", 3), ("2144", 3)]
    assert len(shown) == GROUPS_SHOWN
    assert total == GROUPS_SHOWN + 3


def test_attach_groups_needs_two_groups() -> None:
    result = attach_groups(ToolResult(), [("2142", 4)], 1, {}, "isco-08")
    assert "groups" not in result.meta


def test_attach_groups_names_known_codes_and_keeps_unknown_ones() -> None:
    names = {"2142": {"id": "esco:isco:2142", "label": "Civil engineers"}}
    result = attach_groups(ToolResult(), [("2142", 4), ("9999", 1)], 2, names, "isco-08")
    assert result.meta["groups"] == [
        {"id": "esco:isco:2142", "code": "2142", "label": "Civil engineers", "count": 4},
        {"id": None, "code": "9999", "label": "9999", "count": 1},
    ]
    assert result.meta["group_total"] == 2
    assert result.meta["group_scheme"] == "isco-08"


# -- ESCO: contains reports groups, search_group narrows ----------------------


def test_esco_contains_matches_word_starts_and_reports_groups() -> None:
    civil = _node("esco:occ:civil", "civil engineer")
    mech = _node("esco:occ:mech", "mechanical engineer")
    session = _Session(
        [
            *_NO_EXACT,
            [{"total": 3, "top": [civil, mech], "group_codes": ["2142", "2144", "2144"]}],
            [
                {"code": "2144", "id": "esco:isco:2144", "label": "Mechanical engineers"},
                {"code": "2142", "id": "esco:isco:2142", "label": "Civil engineers"},
            ],
        ]
    )
    result = _suite(EscoSuite, session).search_nodes("engineer", kind="occupation")

    contains_query, params = session.calls[2]
    assert "n.pref_label =~ $word_start" in contains_query
    assert params["word_start"] == word_start_pattern("engineer")
    assert params["group_prefix"] is None
    assert [g["label"] for g in result.meta["groups"]] == [
        "Mechanical engineers",
        "Civil engineers",
    ]
    assert [g["count"] for g in result.meta["groups"]] == [2, 1]


def test_esco_contains_in_one_group_reports_none_and_skips_the_name_query() -> None:
    session = _Session(
        [*_NO_EXACT, [{"total": 1, "top": [_node("a", "civil engineer")], "group_codes": ["2142"]}]]
    )
    result = _suite(EscoSuite, session).search_nodes("civil engineer", kind="occupation")
    assert "groups" not in result.meta
    assert len(session.calls) == 3


def test_esco_search_group_filters_by_isco_prefix() -> None:
    civil = _node("esco:occ:civil", "civil engineer")
    session = _Session([[{"total": 1, "top": [civil], "group_codes": ["2142"]}]])
    result = _suite(EscoSuite, session).search_group("engineer", "2142")

    _query, params = session.calls[0]
    assert params["group_prefix"] == "2142"
    assert params["labels"] == ["Occupation"]
    assert [c.node.label for c in result.candidates] == ["civil engineer"]
    assert result.evidence == ["esco:search:group:2142:engineer"]


@pytest.mark.parametrize(
    ("text", "group", "warning"), [("", "2142", "empty_query"), ("x", "civ", "unknown_group:civ")]
)
def test_esco_search_group_rejects_bad_input(text: str, group: str, warning: str) -> None:
    session = _Session([])
    assert _suite(EscoSuite, session).search_group(text, group).warnings == [warning]
    assert session.calls == []


# -- O*NET: SOC groups, broader skill kind ------------------------------------


def test_onet_contains_names_soc_major_groups() -> None:
    rows = [
        _node("onet:a", "Civil Engineers", "onet"),
        _node("onet:b", "Robotics Engineers", "onet"),
    ]
    session = _Session([*_NO_EXACT, [{"total": 2, "top": rows, "group_codes": ["17", "15"]}]])
    result = _suite(OnetSuite, session).search_nodes("engineer", kind="occupation")
    assert {g["label"] for g in result.meta["groups"]} == {
        "Architecture and Engineering",
        "Computer and Mathematical",
    }
    assert result.meta["group_scheme"] == "soc-2018-major"


def test_onet_search_group_uses_the_soc_code_prefix() -> None:
    row = _node("onet:a", "Civil Engineers", "onet")
    session = _Session([[{"total": 1, "top": [row], "group_codes": ["17"]}]])
    _suite(OnetSuite, session).search_group("engineer", "17")
    assert session.calls[0][1]["group_prefix"] == "17-"


def test_onet_search_group_rejects_an_unknown_code() -> None:
    assert _suite(OnetSuite, _Session([])).search_group("engineer", "99").warnings == [
        "unknown_group:99"
    ]


def test_onet_skill_kind_also_searches_knowledge_activities_and_tasks() -> None:
    session = _Session([[{"node": _node("onet:k", "Economics and Accounting", "onet")}]])
    _suite(OnetSuite, session).search_nodes("Economics and Accounting", kind="skill")
    query = session.calls[0][0]
    for label in ("Skill", "Knowledge", "WorkActivity", "Task"):
        assert f"MATCH (n:{label})" in query


# -- Tier 5: keyword list fused with the vector list ---------------------------


@pytest.mark.parametrize("cls", [EscoSuite, OnetSuite])
def test_tier5_uses_the_keyword_list(cls: type[Any], monkeypatch: Any) -> None:
    import importlib

    module = importlib.import_module(cls.__module__)
    monkeypatch.setattr(module, "_embed_query", lambda _q: None)
    construction = _node("x:construction", "building engineer")
    session = _Session(
        [
            *_NO_EXACT,
            [{"total": 0, "top": [], "group_codes": []}],  # 4) contains
            [{"top": [construction]}],  # 5) keywords
        ]
    )
    result = _suite(cls, session).search_nodes("an engineer who builds buildings")

    _query, params = session.calls[3]
    assert params["lucene"] == "engine* build*"
    assert [c.node.label for c in result.candidates] == ["building engineer"]
    assert [c.method for c in result.candidates] == ["hybrid_rrf"]


@pytest.mark.parametrize("cls", [EscoSuite, OnetSuite])
def test_tier5_without_topic_words_runs_no_keyword_query(cls: type[Any], monkeypatch: Any) -> None:
    import importlib

    module = importlib.import_module(cls.__module__)
    monkeypatch.setattr(module, "_embed_query", lambda _q: None)
    session = _Session([*_NO_EXACT, [{"total": 0, "top": [], "group_codes": []}]])
    result = _suite(cls, session).search_nodes("who are you")
    assert "not_found" in result.warnings
    assert len(session.calls) == 3


# -- acronym second opinion for alias substrings -------------------------------


@pytest.mark.parametrize("cls", [EscoSuite, OnetSuite])
def test_acronym_found_only_inside_an_alias_gets_a_second_opinion(
    cls: type[Any], monkeypatch: Any
) -> None:
    import importlib

    module = importlib.import_module(cls.__module__)
    monkeypatch.setattr(module, "_embed_query", lambda _q: [0.0])
    localiser = _node("x:localiser", "localiser")  # alias "localisation QA tester"
    tester = _node("x:tester", "software tester")
    session = _Session(
        [
            *_NO_EXACT,
            [{"total": 1, "top": [localiser], "group_codes": []}],  # 4) alias substring
            [{"node": tester}],  # meaning search
        ]
    )
    result = _suite(cls, session).search_nodes("QA tester", kind="occupation")

    assert [c.node.label for c in result.candidates] == ["localiser", "software tester"]
    assert [c.method for c in result.candidates] == ["contains", "hybrid_rrf"]
    assert {"alias_unconfirmed", "ambiguous"} <= set(result.warnings)


@pytest.mark.parametrize("cls", [EscoSuite, OnetSuite])
def test_acronym_in_a_title_needs_no_second_opinion(cls: type[Any], monkeypatch: Any) -> None:
    import importlib

    module = importlib.import_module(cls.__module__)
    monkeypatch.setattr(module, "_embed_query", lambda _q: pytest.fail("no meaning search"))
    session = _Session(
        [
            *_NO_EXACT,
            [{"total": 1, "top": [_node("x:ict", "ICT help desk agent")], "group_codes": []}],
        ]
    )
    result = _suite(cls, session).search_nodes("ICT help", kind="occupation")
    assert [c.method for c in result.candidates] == ["contains"]


def test_one_stray_word_is_not_a_keyword_match() -> None:
    from ta_taxonomies.suites._keywords import enough_words, topic_stems

    stems = topic_stems("xyzzy-nonexistent-occupation")
    assert stems == ["xyzzy", "nonexistent"]
    assert not enough_words(stems, ["occupational therapist"])
    two = topic_stems("an engineer who builds bridges")
    assert enough_words(two, ["bridge engineer"])
    assert not enough_words(two, ["engine fitter"])
    assert enough_words(topic_stems("plumbing"), ["plumber"])  # shared stem "plumb"
    assert enough_words(topic_stems("plumber"), ["plumber"])


def test_a_ruled_out_word_is_not_searched_for() -> None:
    from ta_taxonomies.suites._keywords import topic_stems

    assert topic_stems("works with computers but not coding") == ["comput"]
    assert topic_stems("a job without travel, no night shifts") == ["shift"]


def test_a_lone_meaning_hit_is_still_the_users_to_confirm(monkeypatch: Any) -> None:
    import importlib

    module = importlib.import_module(EscoSuite.__module__)
    monkeypatch.setattr(module, "_embed_query", lambda _q: [0.0])
    session = _Session(
        [
            *_NO_EXACT,
            [{"total": 0, "top": [], "group_codes": []}],
            [{"top": []}],
            [{"node": _node("x:math", "mathematician")}],
        ]
    )
    result = _suite(EscoSuite, session).search_nodes("I like math but not coding")
    assert [c.method for c in result.candidates] == ["hybrid_rrf"]
    assert "ambiguous" in result.warnings


def test_an_acronym_alone_matches_whole_words_only() -> None:
    assert word_start_pattern("AI") == r"(?su).*(?<![\p{L}\p{N}])AI(?![\p{L}\p{N}]).*"
    # Lowercase or longer text keeps the word-start match.
    assert word_start_pattern("ai").endswith("ai.*")
    assert word_start_pattern("AI engineer").endswith("AI engineer.*")


def test_a_pasted_paragraph_skips_the_wildcard_index() -> None:
    from ta_taxonomies.suites._locate import MAX_INFIX_TERMS, lucene_infix

    paragraph = " ".join(f"word{i}" for i in range(MAX_INFIX_TERMS + 1))
    assert lucene_infix(paragraph, 3) is None
    assert lucene_infix("data scien", 3) == "+*data* +*scien*"


@pytest.mark.parametrize(
    ("cls", "query", "codes"),
    [
        (OnetSuite, "15-1252.00", ["15-1252.00"]),
        (OnetSuite, "15-1252", ["15-1252.00"]),
        (EscoSuite, "2512", ["2512"]),
        (EscoSuite, "2512.4", ["2512.4"]),
    ],
)
def test_a_code_is_looked_up_before_any_label(cls: type[Any], query: str, codes: list[str]) -> None:
    session = _Session([[{"node": _node("x:dev", "Software Developers")}]])
    result = _suite(cls, session).search_nodes(query)
    _query, params = session.calls[0]
    assert params["codes"] == codes
    assert [c.method for c in result.candidates] == ["exact_code"]


@pytest.mark.parametrize("query", ["15-12520", "2512a", "nurse", "12345"])
def test_text_that_is_not_a_code_skips_the_code_lookup(query: str) -> None:
    from ta_taxonomies.suites.esco.tools import ESCO_LOCATE
    from ta_taxonomies.suites.onet.tools import ONET_LOCATE

    assert ONET_LOCATE.codes_for is not None and ESCO_LOCATE.codes_for is not None
    assert ONET_LOCATE.codes_for(query) == []
    if query != "12345":
        assert ESCO_LOCATE.codes_for(query) == []


def test_isco_group_codes_are_one_to_four_ascii_digits() -> None:
    from ta_taxonomies.suites.esco.tools import ESCO_LOCATE

    assert ESCO_LOCATE.groups is not None
    prefix_for = ESCO_LOCATE.groups.prefix_for
    assert prefix_for("2142") == "2142"
    assert prefix_for("21") == "21"
    for bad in ("99999999", "\u0662\u0665", "21 42", ""):
        assert prefix_for(bad) is None, bad


def test_an_unconfirmed_alias_counts_every_match_and_shows_one_page(monkeypatch: Any) -> None:
    from ta_taxonomies.suites.esco import tools

    monkeypatch.setattr(tools, "_embed_query", lambda _q: [0.0])
    alias_hits = [_node(f"x:a{i}", f"title {i}") for i in range(25)]
    meaning = [{"node": _node(f"x:m{i}", f"meaning {i}")} for i in range(25)]
    session = _Session(
        [
            *_NO_EXACT,
            [{"total": 40, "top": alias_hits, "group_codes": []}],  # 40 alias-only matches
            meaning,
        ]
    )
    result = _suite(EscoSuite, session).search_nodes("QA tester")
    assert len(result.candidates) == 25
    assert result.meta["matches"] == 40 + 25
    assert "truncated" in result.warnings


def test_one_word_left_after_a_dropped_acronym_is_no_keyword_query() -> None:
    assert keyword_query("IT manager") is None
    assert keyword_query("UX designer") is None
    assert keyword_query("a manager") == "manag*"
    assert keyword_query("is it a manager") == "manag*"
    assert keyword_query("plumber") == "plumb*"
