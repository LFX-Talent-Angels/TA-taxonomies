"""Latency regression for search_nodes on an ESCO-scale graph.

Opt-in: it needs its own Neo4j, because it wipes and rebuilds ~21k nodes.
Point ``TA_PERF_NEO4J_URI`` at a throwaway instance::

    docker run -d --name ta-neo4j-perf -p 7689:7687 \\
        -e NEO4J_AUTH=neo4j/perf-dev neo4j:5-community
    TA_PERF_NEO4J_URI=bolt://localhost:7689 \\
    TA_PERF_NEO4J_PASSWORD=perf-dev pytest tests/perf -q

The variable is deliberately not ``NEO4J_URI``: a perf test that wipes
whatever the default connection happens to point at is a trap.

What it asserts, and why it asserts it that way: wall-clock thresholds are
useless on a shared machine, so the guard is *relative* (the indexed path
against the scan it replaced, in the same process, on the same graph) and
*structural* (the query plan must reach the index). Both survive a slow CI box;
neither survives someone reintroducing the umbrella-label scan.
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Iterator
from typing import Any

import pytest
from neo4j import Driver, GraphDatabase

from ta_taxonomies.suites.esco.config import (
    LABEL_ISCO_GROUP,
    LABEL_OCCUPATION,
    LABEL_SKILL,
    LABEL_SKILL_GROUP,
    SEARCH_LIMIT,
    SEARCH_SCAN_CAP,
    SOURCE,
)
from ta_taxonomies.suites.esco.load import (
    _dedupe_edges,
    load_normalized,
    normalize_document,
    validate_load,
)
from ta_taxonomies.suites.esco.tools import _SCAN_CONTAINS, EscoSuite, _exact_pref_cypher

from .synthetic_esco import build_document

_URI = os.getenv("TA_PERF_NEO4J_URI")

pytestmark = pytest.mark.skipif(
    not _URI,
    reason="set TA_PERF_NEO4J_URI (a throwaway Neo4j — this test wipes the graph)",
)

LABELS = [LABEL_OCCUPATION, LABEL_SKILL, LABEL_ISCO_GROUP, LABEL_SKILL_GROUP]
# Queries that landed in the substring tier, i.e. the expensive ones.
LOOSE_QUERIES = ["data scien", "machine learn", "engineer", "renewable energy", "cloud"]


@pytest.fixture(scope="module")
def perf_graph() -> Iterator[tuple[Driver, str]]:
    database = os.getenv("TA_PERF_NEO4J_DATABASE", "neo4j")
    driver = GraphDatabase.driver(
        str(_URI),
        auth=(
            os.getenv("TA_PERF_NEO4J_USER", "neo4j"),
            os.getenv("TA_PERF_NEO4J_PASSWORD", ""),
        ),
    )
    driver.verify_connectivity()

    payload = normalize_document(build_document())
    for key in ("occupations", "skills", "isco_groups", "skill_groups"):
        unique: dict[str, dict[str, Any]] = {row["id"]: row for row in payload[key]}
        payload[key] = list(unique.values())
    for key in ("has_skill", "broader", "classified", "related_to"):
        payload[key] = _dedupe_edges(payload.get(key) or [])

    counts = load_normalized(driver, payload, database=database, wipe=True)
    validate_load(
        driver,
        {
            "occupations": counts["occupations"],
            "skills": counts["skills"],
            "isco_groups": counts["isco_groups"],
            "skill_groups": counts["skill_groups"],
            "has_skill": len(payload["has_skill"]),
            "broader_than": len(payload["broader"]),
            "classified_under": len(payload["classified"]),
            "related_to": len(payload["related_to"]),
        },
        database=database,
    )
    yield driver, database
    driver.close()


def _plan_operators(driver: Driver, database: str, cypher: str, **params: Any) -> set[str]:
    with driver.session(database=database) as session:
        result = session.run("EXPLAIN " + cypher, **params)
        plan = result.consume().plan

    operators: set[str] = set()

    def walk(node: dict[str, Any]) -> None:
        operators.add(str(node["operatorType"]).split("@")[0])
        for child in node.get("children", []):
            walk(child)

    walk(plan)
    return operators


def test_exact_match_reaches_the_index_instead_of_scanning(
    perf_graph: tuple[Driver, str],
) -> None:
    driver, database = perf_graph

    operators = _plan_operators(
        driver,
        database,
        _exact_pref_cypher(LABELS),
        q="quality assurance specialist",
        source=SOURCE,
        scan_cap=SEARCH_SCAN_CAP,
    )

    assert "NodeIndexSeek" in operators
    assert "NodeByLabelScan" not in operators


def test_loose_match_is_faster_than_the_scan_it_replaced(
    perf_graph: tuple[Driver, str],
) -> None:
    """Compare the shipped path against the fallback scan, same process, same graph."""
    driver, database = perf_graph
    suite = EscoSuite(driver, database=database)

    indexed: list[float] = []
    scanned: list[float] = []
    with driver.session(database=database) as session:
        for query in LOOSE_QUERIES:
            params = {
                "q": query,
                "labels": LABELS,
                "source": SOURCE,
                "limit": SEARCH_LIMIT,
                "scan_cap": SEARCH_SCAN_CAP,
            }
            suite.search_nodes(query)
            session.run(_SCAN_CONTAINS, **params).single()
            for _ in range(5):
                start = time.perf_counter()
                suite.search_nodes(query)
                indexed.append(time.perf_counter() - start)

                start = time.perf_counter()
                session.run(_SCAN_CONTAINS, **params).single()
                scanned.append(time.perf_counter() - start)

    # 2x is a floor, not a target: the paired runs above measured ~4x. A
    # regression that puts the umbrella scan back lands well under 1x.
    assert statistics.median(scanned) > 2 * statistics.median(indexed)


def test_truncated_answers_report_what_they_cut(perf_graph: tuple[Driver, str]) -> None:
    driver, database = perf_graph
    suite = EscoSuite(driver, database=database)

    result = suite.search_nodes("engineer")

    assert len(result.candidates) == SEARCH_LIMIT
    assert "truncated" in result.warnings
    assert result.pruning is not None
    assert result.pruning.pruned > 0
    assert result.pruning.considered == result.meta["matches"] > SEARCH_LIMIT
