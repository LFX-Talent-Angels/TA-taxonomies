"""search_nodes / get_neighbors / enumerate_paths against the JobTech fixture in Neo4j.

Skipped when Neo4j is not reachable. Loads into whatever NEO4J_* names and
wipes JobTech nodes there first.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.jobtech.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.jobtech.load import run_load
from ta_taxonomies.suites.jobtech.tools import JobtechSuite


def _neo4j_available() -> bool:
    if not os.getenv("NEO4J_PASSWORD"):
        return False
    try:
        with neo4j_driver() as (driver, _db):
            verify_connectivity(driver)
        return True
    except (ServiceUnavailable, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _neo4j_available(),
    reason="Neo4j not available (start with: docker compose up -d)",
)


@pytest.fixture(autouse=True)
def _no_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the meaning tier off: these tests must not load an ML model."""
    monkeypatch.setattr("ta_taxonomies.suites.jobtech.tools._embed_query", lambda _q: None)


@pytest.fixture(scope="module")
def suite() -> Iterator[JobtechSuite]:
    from neo4j import GraphDatabase

    from ta_taxonomies.suites.jobtech.db import neo4j_config_from_env

    run_load(mode="fixture", wipe=True)
    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    yield JobtechSuite(driver, database=cfg["database"])
    driver.close()


def test_swedish_title_and_alias(suite: JobtechSuite) -> None:
    exact = suite.search_nodes("Barnmorska", kind="occupation")
    assert (exact.candidates[0].method, exact.candidates[0].node.id) == (
        "exact_pref",
        "jobtech:occupation:DEDi_wap_ntF",
    )
    alias = suite.search_nodes("Bagare", kind="occupation")
    assert alias.candidates[0].node.id == "jobtech:occupation:XugJ_6hw_wVV"
    props = alias.candidates[0].node.properties
    assert props["ssyk_code"] == "7612"
    assert props["isco_codes"] == ["7512"]
    assert props["esco_exact"]


def test_casefold_with_swedish_letters(suite: JobtechSuite) -> None:
    result = suite.search_nodes("sjuksköterska, GRUNDUTBILDAD", kind="occupation")
    assert result.candidates[0].node.id == "jobtech:occupation:bXNH_MNX_dUR"


def test_english_finds_nothing_by_words(suite: JobtechSuite) -> None:
    # The known limit: labels are Swedish. "baker" is not a word in "Bagare".
    assert "not_found" in suite.search_nodes("baker", kind="occupation").warnings


def test_false_friend_chef_never_finds_the_cook(suite: JobtechSuite) -> None:
    # Swedish "chef" means manager; the cook is "kock". "Restaurangchef" is one
    # compound word, so word-start matching does not reach it either.
    labels = [c.node.label for c in suite.search_nodes("chef", kind="occupation").candidates]
    assert "Kock à la carte" not in labels


def test_compound_words_hide_their_parts(suite: JobtechSuite) -> None:
    # Known limit (Swedish compounds): "utvecklare" does not start a word in
    # "Mjukvaruutvecklare", so it is not found by words.
    labels = [c.node.label for c in suite.search_nodes("utvecklare").candidates]
    assert "Mjukvaruutvecklare" not in labels


def test_codes(suite: JobtechSuite) -> None:
    assert [c.node.id for c in suite.search_nodes("2222").candidates] == ["jobtech:ssyk:2222"]
    by_id = suite.search_nodes("XugJ_6hw_wVV")
    assert [(c.method, c.node.label) for c in by_id.candidates] == [
        ("exact_code", "Bagare/Konditor")
    ]


def test_neighbours_put_jobtech_essentials_first(suite: JobtechSuite) -> None:
    result = suite.get_neighbors("jobtech:occupation:DEDi_wap_ntF", ["HAS_SKILL"])
    kinds = [e.properties["relation_type"] for e in result.edges]
    assert kinds[:2] == ["essential", "essential"]
    order = {"essential": 0, "optional": 1, "group": 2}
    assert [order[k] for k in kinds] == sorted(order[k] for k in kinds)


def test_substitution_is_a_route(suite: JobtechSuite) -> None:
    result = suite.enumerate_paths(
        "jobtech:occupation:Sbzf_iBA_MAS", "jobtech:occupation:17FS_XzU_AQ4", max_depth=1
    )
    # JobTech lists the pair both ways, so both edges join the two occupations.
    assert {p.edges[0].type for p in result.paths} == {"SUBSTITUTES"}
    assert {p.edges[0].properties["substitutability_pct"] for p in result.paths} == {75}


def test_bad_input(suite: JobtechSuite) -> None:
    assert suite.search_nodes("bagare", kind="task").warnings == ["unknown_kind:task"]
    assert suite.get_neighbors("jobtech:occupation:nope").warnings == ["node_not_found"]
