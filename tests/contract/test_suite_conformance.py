"""Every suite behaves the same way. A new suite is done when this file is green.

Adding a suite? Add one ``SuiteCase`` to ``_cases()`` below. Nothing else in this
file changes: the same checks run against every suite.

Two layers:

* **Offline** (always, in CI): the suite's ``LocateConfig`` and class follow
  the shared rules — one confidence scale, known labels only, a contract suite
  name, the full tool set, group codes that cannot carry Cypher.
* **Live** (opt-in): the suite's committed fixture is loaded into a throwaway
  Neo4j and searched. It wipes that database, so it only runs when
  ``TA_CONFORMANCE_NEO4J_URI`` is set (never ``NEO4J_URI``)::

      docker run -d --rm --name ta-neo4j-test -p 7688:7687 \\
          -e NEO4J_AUTH=neo4j/test-password neo4j:5-community
      TA_CONFORMANCE_NEO4J_URI=bolt://localhost:7688 \\
      TA_CONFORMANCE_NEO4J_PASSWORD=test-password pytest tests/contract -q
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, get_args

import pytest

from ta_taxonomies.contract.models import SuiteName, ToolResult
from ta_taxonomies.contract.schema import SuiteSchema
from ta_taxonomies.suites._locate import Confidences, LocateConfig, Locator


@dataclass(frozen=True)
class SuiteCase:
    """What the shared checks need to know about one suite."""

    name: str
    config: LocateConfig
    #: ``suite_class(driver, database=...)`` builds the suite.
    suite_class: type[Any]
    #: Loads the suite's committed fixture into the database named by NEO4J_*.
    load_fixture: Callable[[], object]
    #: An occupation title that is in the fixture, exactly as stored.
    exact_title: str
    #: A word that starts the titles of two or more fixture occupations.
    broad_query: str
    #: A well-formed group code (None when the suite has no group scheme).
    sample_group_code: str | None
    #: A query whose title matches span two groups or more in the fixture, so
    #: groups and narrowing are checked live. None only when the fixture
    #: cannot hold one; then groups are checked offline only.
    group_query: str | None = None
    #: A code the fixture holds and the title it names (None without codes).
    sample_code: tuple[str, str] | None = None


def _load_esco() -> object:
    from ta_taxonomies.suites.esco.load import run_load

    return run_load(mode="fixture", wipe=True)


def _load_onet() -> object:
    from ta_taxonomies.suites.onet.load import run_load

    return run_load(mode="fixture", wipe=True)


def _cases() -> list[SuiteCase]:
    from ta_taxonomies.suites.esco.tools import ESCO_LOCATE, EscoSuite
    from ta_taxonomies.suites.onet.tools import ONET_LOCATE, OnetSuite

    return [
        SuiteCase(
            name="esco",
            config=ESCO_LOCATE,
            suite_class=EscoSuite,
            load_fixture=_load_esco,
            exact_title="software developer",
            broad_query="developer",
            sample_group_code="2512",
            group_query="developer",
        ),
        SuiteCase(
            name="onet",
            config=ONET_LOCATE,
            suite_class=OnetSuite,
            load_fixture=_load_onet,
            exact_title="Software Developers",
            broad_query="software",
            sample_group_code="15",
            # Every fixture title sharing a word sits in SOC 15: no live group query.
            group_query=None,
            sample_code=("15-1252.00", "Software Developers"),
        ),
        # Add your suite here.
    ]


CASES = _cases()
IDS = [case.name for case in CASES]
METHODS = {
    "exact_code",
    "exact_pref",
    "exact_alt",
    "casefold_pref",
    "casefold_pref_ambiguous",
    "contains",
    "hybrid_rrf",
}


def _starts_a_word(query: str, text: str) -> bool:
    return re.search(rf"(?<![^\W_]){re.escape(query.casefold())}", text.casefold()) is not None


# -- offline -------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_source_is_a_contract_suite_name(case: SuiteCase) -> None:
    assert case.config.source in get_args(SuiteName)
    assert case.config.source == case.name


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_suite_uses_the_one_confidence_scale(case: SuiteCase) -> None:
    # The scale says how a match was made; it means the same in every suite.
    assert case.config.confidences == Confidences()


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_occupation_and_skill_kinds_resolve(case: SuiteCase) -> None:
    locator = Locator(case.config, session=lambda: None, embed=lambda _q: None)  # type: ignore[arg-type,return-value]
    occupation = locator.labels_for("occupation")
    skill = locator.labels_for("skill")
    assert occupation == [case.config.occupation_label]
    assert skill
    assert locator.labels_for("no-such-kind") is None


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_only_the_suites_own_labels_reach_cypher(case: SuiteCase) -> None:
    config = case.config
    assert config.occupation_label in config.default_labels
    assert set(config.default_labels) <= config.searchable_labels
    for label in [*config.searchable_labels, config.node_label]:
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", label), label


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_index_names_follow_the_convention(case: SuiteCase) -> None:
    assert case.config.vector_index == f"{case.name}_label_embedding"
    assert case.config.fulltext_index


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_group_codes_are_checked_before_they_reach_cypher(case: SuiteCase) -> None:
    groups = case.config.groups
    if groups is None:
        assert case.sample_group_code is None
        return
    assert case.sample_group_code is not None
    assert isinstance(groups.prefix_for(case.sample_group_code), str)
    for hostile in ("", "x' OR 1=1", "15; MATCH (n) DETACH DELETE n", "../.."):
        assert groups.prefix_for(hostile) is None


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_suite_offers_every_tool(case: SuiteCase) -> None:
    for tool in (
        "search_nodes",
        "search_group",
        "get_neighbors",
        "enumerate_paths",
        "score_paths",
    ):
        assert callable(getattr(case.suite_class, tool, None)), tool
    suite = object.__new__(case.suite_class)
    assert isinstance(suite.suite_schema, SuiteSchema)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_bad_input_is_answered_without_the_database(case: SuiteCase) -> None:
    suite = object.__new__(case.suite_class)
    suite._session = lambda: pytest.fail("no query for bad input")
    assert suite.search_nodes("   ").warnings == ["empty_query"]
    assert suite.search_nodes("nurse", kind="spaceship").warnings == ["unknown_kind:spaceship"]
    assert suite.search_group("nurse", "x' OR 1=1").warnings == ["unknown_group:x' OR 1=1"]


# -- live, on a throwaway database ------------------------------------------------

_URI = os.getenv("TA_CONFORMANCE_NEO4J_URI")
live = pytest.mark.skipif(
    not _URI,
    reason="set TA_CONFORMANCE_NEO4J_URI (a throwaway Neo4j — this wipes the graph)",
)


@pytest.fixture(scope="module", params=CASES, ids=IDS)
def loaded(request: pytest.FixtureRequest) -> Iterator[tuple[SuiteCase, Any]]:
    from neo4j import GraphDatabase

    case: SuiteCase = request.param
    env = {
        "NEO4J_URI": _URI or "",
        "NEO4J_USER": os.getenv("TA_CONFORMANCE_NEO4J_USER", "neo4j"),
        "NEO4J_PASSWORD": os.getenv("TA_CONFORMANCE_NEO4J_PASSWORD", ""),
        # The loader and the searching driver must use the same database.
        "NEO4J_DATABASE": os.getenv("TA_CONFORMANCE_NEO4J_DATABASE", "neo4j"),
    }
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        case.load_fixture()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"]))
    try:
        yield case, case.suite_class(driver, database=env["NEO4J_DATABASE"])
    finally:
        driver.close()


def _check_candidates(case: SuiteCase, result: ToolResult) -> None:
    conf = case.config.confidences
    declared = {
        "exact_code": conf.exact_pref,
        "exact_pref": conf.exact_pref,
        "exact_alt": conf.exact_alt,
        "casefold_pref": conf.casefold_unique,
        "casefold_pref_ambiguous": conf.casefold_ambiguous,
        "contains": conf.contains,
        "hybrid_rrf": conf.hybrid,
    }
    assert isinstance(result, ToolResult)
    for candidate in result.candidates:
        assert candidate.method in METHODS
        assert candidate.confidence == declared[candidate.method]
        assert candidate.node.source == case.name
    assert all(pointer.startswith(f"{case.name}:") for pointer in result.evidence)
    if result.pruning is not None:
        assert result.pruning.returned == len(result.candidates)


@live
def test_exact_title_is_found_exactly(loaded: tuple[SuiteCase, Any]) -> None:
    case, suite = loaded
    result = suite.search_nodes(case.exact_title, kind="occupation")
    _check_candidates(case, result)
    assert [c.method for c in result.candidates] == ["exact_pref"]
    assert result.candidates[0].node.label == case.exact_title


@live
def test_nonsense_is_not_found(loaded: tuple[SuiteCase, Any]) -> None:
    case, suite = loaded
    result = suite.search_nodes("zzqxv plonkwurst", kind="occupation")
    assert "not_found" in result.warnings
    assert result.candidates == []


@live
def test_broad_query_matches_only_at_word_starts(loaded: tuple[SuiteCase, Any]) -> None:
    case, suite = loaded
    result = suite.search_nodes(case.broad_query, kind="occupation")
    _check_candidates(case, result)
    assert len(result.candidates) >= 2
    assert "ambiguous" in result.warnings
    for candidate in result.candidates:
        names = [candidate.node.label, *(candidate.node.properties.get("alt_labels") or [])]
        assert any(_starts_a_word(case.broad_query, name) for name in names), candidate.node.label


@live
def test_a_fragment_inside_a_word_does_not_match(loaded: tuple[SuiteCase, Any]) -> None:
    case, suite = loaded
    word = next(w for w in case.exact_title.split() if len(w) >= 5)
    fragment = word[1:4]  # "oft" from "software": inside a word, never its start
    result = suite.search_nodes(fragment, kind="occupation")
    assert case.exact_title not in [c.node.label for c in result.candidates]


@live
def test_groups_are_reported_and_narrow(loaded: tuple[SuiteCase, Any]) -> None:
    case, suite = loaded
    if case.config.groups is None or case.group_query is None:
        pytest.skip("no group query for this fixture; groups are checked offline")
    result = suite.search_nodes(case.group_query, kind="occupation")
    groups = result.meta.get("groups")
    assert groups, "a group query must report meta.groups"
    for group in groups:
        assert set(group) == {"id", "code", "label", "count"}
        assert group["count"] >= 1
    counts = [group["count"] for group in groups]
    assert counts == sorted(counts, reverse=True)
    narrowed = suite.search_group(case.group_query, groups[0]["code"])
    _check_candidates(case, narrowed)
    assert narrowed.candidates


@live
def test_a_code_names_its_record(loaded: tuple[SuiteCase, Any]) -> None:
    case, suite = loaded
    if case.sample_code is None:
        pytest.skip("no code in this fixture")
    code, title = case.sample_code
    result = suite.search_nodes(code)
    _check_candidates(case, result)
    assert [(c.method, c.node.label) for c in result.candidates] == [("exact_code", title)]


@live
def test_neighbours_of_a_found_node(loaded: tuple[SuiteCase, Any]) -> None:
    case, suite = loaded
    node = suite.search_nodes(case.exact_title, kind="occupation").candidates[0].node
    assert isinstance(suite.get_neighbors(node.id), ToolResult)
    assert "node_not_found" in suite.get_neighbors(f"{case.name}:no-such-node").warnings
