"""Measurement harness for ``search_nodes`` latency and answer quality.

Use case: run the same frozen query set against the same graph before and after
a change, so "faster" is a number and not an impression. Reports median / p95
wall time for the whole tool call (driver round trips included), how often the
answer is ambiguous, and how often the result hits the declared result limit.

Why it exists: ARCHITECTURE requires bounded tools to report what they cut. You
cannot argue about a cut you have not counted, and you cannot argue about a
speed-up you have not measured on a graph the size of the real classification.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any

from neo4j import GraphDatabase

from ta_taxonomies.suites.esco.tools import EscoSuite

# Frozen query set: same inputs before and after, or the comparison is worthless.
# Grouped by the branch of search_nodes each query is expected to land in.
QUERIES: list[tuple[str, str, str | None]] = [
    # (query text, expected branch, kind filter)
    ("quality assurance specialist", "exact_pref", "occupation"),
    ("research administrator", "exact_pref", "occupation"),
    ("telecommunications designer", "exact_pref", "occupation"),
    ("manufacturing architect", "exact_pref", "occupation"),
    ("forecast marketing", "exact_pref", "skill"),
    ("optimise infrastructure", "exact_pref", "skill"),
    ("Quality Assurance Specialist", "casefold", "occupation"),
    ("Research Administrator", "casefold", "occupation"),
    ("Manufacturing Architect", "casefold", "occupation"),
    ("Forecast Marketing", "casefold", "skill"),
    ("research administrator aviation", "exact_alt", "occupation"),
    ("quality assurance specialist aerospace", "exact_alt", "occupation"),
    ("telecommunications designer marine", "exact_alt", "occupation"),
    ("insurance telecommunications designer", "exact_alt", "occupation"),
    ("data scien", "contains", "occupation"),
    ("network engin", "contains", "occupation"),
    ("machine learn", "contains", None),
    ("cloud", "contains", None),
    ("data", "contains", None),
    ("engineer", "contains", "occupation"),
    ("manage data", "contains", "skill"),
    ("security analyst", "contains", "occupation"),
    ("aerospace", "contains", "occupation"),
    ("business intelligence", "contains", None),
    ("optimise", "contains", "skill"),
    ("renewable energy", "contains", None),
    ("zzznofuchoccupation999", "not_found", "occupation"),
    ("quantum tea ceremony architect", "not_found", None),
    ("xyzzy plugh", "not_found", None),
    ("C++", "not_found", None),  # Lucene metacharacters must not blow up
]


@dataclass
class QueryMeasurement:
    query: str
    kind: str | None
    expected_branch: str
    method: str
    confidence: float | None
    n_candidates: int
    ambiguous: bool
    saturated: bool
    reported_total: int | None
    median_ms: float
    p95_ms: float


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def measure(
    suite: EscoSuite,
    *,
    repeats: int = 15,
    limit: int = 25,
) -> list[QueryMeasurement]:
    """Time every frozen query ``repeats`` times; return per-query statistics."""
    out: list[QueryMeasurement] = []
    for text, expected, kind in QUERIES:
        suite.search_nodes(text, kind=kind)  # warm the page cache for this query
        samples: list[float] = []
        result = None
        for _ in range(repeats):
            start = time.perf_counter()
            result = suite.search_nodes(text, kind=kind)
            samples.append((time.perf_counter() - start) * 1000.0)
        assert result is not None
        top = result.candidates[0] if result.candidates else None
        pruning = result.pruning
        out.append(
            QueryMeasurement(
                query=text,
                kind=kind,
                expected_branch=expected,
                method=top.method if top else "none",
                confidence=top.confidence if top else None,
                n_candidates=len(result.candidates),
                ambiguous="ambiguous" in result.warnings,
                saturated=len(result.candidates) >= limit,
                reported_total=pruning.considered if pruning else None,
                median_ms=statistics.median(samples),
                p95_ms=_percentile(samples, 95),
            )
        )
    return out


def summarize(measurements: list[QueryMeasurement]) -> dict[str, Any]:
    medians = [m.median_ms for m in measurements]
    total = len(measurements)
    return {
        "queries": total,
        "median_ms": round(statistics.median(medians), 3),
        "p95_ms": round(_percentile([m.p95_ms for m in measurements], 95), 3),
        "mean_ms": round(statistics.fmean(medians), 3),
        "max_ms": round(max(medians), 3),
        "ambiguous": sum(1 for m in measurements if m.ambiguous),
        "ambiguous_pct": round(100.0 * sum(1 for m in measurements if m.ambiguous) / total, 1),
        "saturated_limit": sum(1 for m in measurements if m.saturated),
        "reported_truncation": sum(1 for m in measurements if m.reported_total is not None),
    }


def run(uri: str, user: str, password: str, database: str = "neo4j") -> dict[str, Any]:
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        suite = EscoSuite(driver, database=database)
        measurements = measure(suite)
    finally:
        driver.close()
    return {
        "summary": summarize(measurements),
        "queries": [asdict(m) for m in measurements],
    }


def cold_call(uri: str, user: str, password: str, database: str = "neo4j") -> float:
    """Time the very first search on a brand-new driver (no warm caches)."""
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        suite = EscoSuite(driver, database=database)
        start = time.perf_counter()
        suite.search_nodes("data scien", kind="occupation")
        return (time.perf_counter() - start) * 1000.0
    finally:
        driver.close()


if __name__ == "__main__":  # pragma: no cover - developer entrypoint
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark ESCO search_nodes")
    parser.add_argument("--uri", required=True)
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", required=True)
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    report = run(args.uri, args.user, args.password, args.database)
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(json.dumps(report["summary"], indent=2))
