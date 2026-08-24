"""Frozen confidence table for search_nodes against the committed fixture.

Use case: the full-text index changed how Locate *retrieves* candidates. This
is the guard that it did not change what Locate *says* about them. Every row
was captured from the scan-based implementation and must keep holding.

Why a whole file for it: the confidence scale is user-visible. "data scientist"
answering 0.95 and "data scien" answering 0.70 is how a caller tells a hit from
a guess. A retrieval change that quietly moves those numbers is a regression
even when it is faster, so parity is asserted before latency anywhere else.

Skipped when Neo4j is not reachable (CI without Docker).
"""

from __future__ import annotations

import os

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.esco.config import (
    CONF_CASEFOLD_UNIQUE,
    CONF_CONTAINS,
    CONF_EXACT_ALT,
    CONF_EXACT_PREF,
)
from ta_taxonomies.suites.esco.db import neo4j_config_from_env, neo4j_driver, verify_connectivity
from ta_taxonomies.suites.esco.load import run_load
from ta_taxonomies.suites.esco.tools import EscoSuite


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

# (query, kind, method, confidence, candidate count, sorted warnings)
PARITY: list[tuple[str, str | None, str | None, float | None, int, list[str]]] = [
    ("software developer", None, "exact_pref", CONF_EXACT_PREF, 1, []),
    ("software developer", "occupation", "exact_pref", CONF_EXACT_PREF, 1, []),
    ("data scientist", None, "exact_pref", CONF_EXACT_PREF, 1, []),
    ("Haskell", "skill", "exact_pref", CONF_EXACT_PREF, 1, []),
    ("mathematical modelling", "skill", "exact_pref", CONF_EXACT_PREF, 1, []),
    ("incremental development", "skill", "exact_pref", CONF_EXACT_PREF, 1, []),
    ("ICT security administrator", None, "exact_pref", CONF_EXACT_PREF, 1, []),
    # alias hits: the reason the alias index is first-class (users type titles)
    ("programmer", "occupation", "exact_alt", CONF_EXACT_ALT, 1, []),
    ("web developers", "occupation", "exact_alt", CONF_EXACT_ALT, 1, []),
    ("CISO", None, "exact_alt", CONF_EXACT_ALT, 1, []),
    ("DNS", "skill", "exact_alt", CONF_EXACT_ALT, 1, []),
    ("forensic IT", "skill", "exact_alt", CONF_EXACT_ALT, 1, []),
    # case-only difference stays a tier below an exact hit
    ("Software Developer", None, "casefold_pref", CONF_CASEFOLD_UNIQUE, 1, []),
    ("haskell", "skill", "casefold_pref", CONF_CASEFOLD_UNIQUE, 1, []),
    ("ict security administrator", None, "casefold_pref", CONF_CASEFOLD_UNIQUE, 1, []),
    # partial text stays at the loose tier, and says it is ambiguous
    ("data scien", "occupation", "contains", CONF_CONTAINS, 1, []),
    ("ciso", None, "contains", CONF_CONTAINS, 1, []),
    ("developer", "occupation", "contains", CONF_CONTAINS, 2, ["ambiguous"]),
    ("data", None, "contains", CONF_CONTAINS, 11, ["ambiguous"]),
    ("web", None, "contains", CONF_CONTAINS, 6, ["ambiguous"]),
    # Lucene metacharacters must resolve, not raise and not silently miss
    ("C++", None, "contains", CONF_CONTAINS, 1, []),
    ("+++", None, None, None, 0, ["not_found"]),
    ("zzz-no-such-thing", None, None, None, 0, ["not_found"]),
]


@pytest.fixture(scope="module")
def loaded_suite() -> EscoSuite:
    from neo4j import GraphDatabase

    run_load(mode="fixture", wipe=True)
    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    suite = EscoSuite(driver, database=cfg["database"])
    yield suite
    driver.close()


@pytest.mark.parametrize(
    ("query", "kind", "method", "confidence", "count", "warnings"),
    PARITY,
    ids=[f"{query}|{kind}" for query, kind, *_ in PARITY],
)
def test_confidence_is_unchanged(
    loaded_suite: EscoSuite,
    query: str,
    kind: str | None,
    method: str | None,
    confidence: float | None,
    count: int,
    warnings: list[str],
) -> None:
    result = loaded_suite.search_nodes(query, kind=kind)

    assert len(result.candidates) == count
    assert sorted(result.warnings) == warnings
    if method is None:
        assert result.candidates == []
        return
    assert {candidate.method for candidate in result.candidates} == {method}
    assert {candidate.confidence for candidate in result.candidates} == {confidence}


def test_every_result_reports_its_match_count(loaded_suite: EscoSuite) -> None:
    """A capped answer has to say so; an uncapped one still reports the total."""
    result = loaded_suite.search_nodes("data")

    assert result.pruning is not None
    assert result.pruning.returned == len(result.candidates)
    assert result.pruning.considered == result.meta["matches"]
    assert result.pruning.pruned == 0  # fixture is too small to hit the limit
    assert "truncated" not in result.warnings


def test_search_still_works_without_the_fulltext_index(loaded_suite: EscoSuite) -> None:
    """Graphs loaded before this index existed must degrade, not break."""
    from ta_taxonomies.suites.esco.config import FULLTEXT_INDEX

    with loaded_suite._session() as session:
        session.run(f"DROP INDEX {FULLTEXT_INDEX} IF EXISTS")
    try:
        result = loaded_suite.search_nodes("data scien", kind="occupation")
        assert [c.confidence for c in result.candidates] == [CONF_CONTAINS]
        assert "fulltext_index_missing" in result.warnings
    finally:
        from ta_taxonomies.suites.esco.schema import FULLTEXT_INDEXES

        with loaded_suite._session() as session:
            for statement in FULLTEXT_INDEXES:
                session.run(statement)
