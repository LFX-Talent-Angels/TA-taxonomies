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


def test_a_part_of_a_compound_is_offered(suite: JobtechSuite) -> None:
    # Swedish compounds: "utvecklare" is not a word start in "Mjukvaruutvecklare",
    # so the compound tier finds it, always as a list to choose from.
    result = suite.search_nodes("utvecklare", kind="occupation")
    labels = {c.node.label for c in result.candidates}
    assert {"Mjukvaruutvecklare", "Systemutvecklare/Programmerare"} <= labels
    assert "ambiguous" in result.warnings
    assert "compound_part" in result.warnings
    assert {c.method for c in result.candidates} == {"contains"}


def test_compound_titles_join_word_start_titles(suite: JobtechSuite) -> None:
    # "assistent" starts a word in two titles and sits inside "Elevassistent".
    labels = [c.node.label for c in suite.search_nodes("assistent", kind="occupation").candidates]
    assert labels[:2] == ["AD-assistent", "Assistent i församlingsarbete"] or set(labels[:2]) == {
        "AD-assistent",
        "Assistent i församlingsarbete",
    }
    assert "Elevassistent" in labels[2:]


def test_short_fragments_never_match_inside_words(suite: JobtechSuite) -> None:
    assert "not_found" in suite.search_nodes("utv", kind="occupation").warnings


def test_a_compound_part_never_becomes_one_answer(suite: JobtechSuite) -> None:
    # "morska" only appears inside "Barnmorska": still a list of one, not a bind.
    result = suite.search_nodes("morska", kind="occupation")
    assert [c.node.label for c in result.candidates] == ["Barnmorska"]
    assert "ambiguous" in result.warnings


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


BAKER = "http://data.europa.eu/esco/occupation/1aadb308-432a-4d01-b54b-b4f7f76dd419"


def test_esco_match_reaches_the_swedish_record(suite: JobtechSuite) -> None:
    # ESCO "baker" (found in English) -> JobTech's own exact match -> Bagare/Konditor.
    result = suite.search_by_esco([BAKER])
    assert [(c.method, c.node.label) for c in result.candidates] == [
        ("esco_exact_match", "Bagare/Konditor")
    ]
    assert result.candidates[0].confidence == 0.65
    assert result.evidence == [f"jobtech:esco_match:{BAKER}"]


def test_esco_match_guards(suite: JobtechSuite) -> None:
    assert suite.search_by_esco(["http://data.europa.eu/esco/occupation/nope"]).warnings == [
        "not_found"
    ]
    assert suite.search_by_esco(["x' OR 1=1"]).warnings == ["empty_query"]
    assert suite.search_by_esco([BAKER], kind="task").warnings == ["unknown_kind:task"]


def test_an_ssyk_code_is_found_whatever_kind_is_asked(suite: JobtechSuite) -> None:
    for text in ("7612", "SSYK 7612"):
        result = suite.search_nodes(text, kind="occupation")
        assert [c.node.id for c in result.candidates] == ["jobtech:ssyk:7612"], text
