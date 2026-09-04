"""Unit tests for how Locate builds its queries — no Neo4j required.

These cover the parts that decide *whether the index is usable at all*: term
extraction, Lucene escaping, and the concrete-label seek. Getting one of them
wrong is silent — the query still runs, it just scans.
"""

from __future__ import annotations

from typing import Any

import pytest

from ta_taxonomies.contract.models import PruningStats
from ta_taxonomies.suites.esco.config import (
    CONF_CONTAINS,
    LABEL_OCCUPATION,
    LABEL_SKILL,
    SEARCH_LIMIT,
)
from ta_taxonomies.suites.esco.tools import (
    _exact_pref_cypher,
    _locate_result,
    _lucene_infix,
    _lucene_phrase,
    _query_terms,
)


def test_terms_ignore_punctuation_and_case() -> None:
    assert _query_terms("Data Scientist") == ["data", "scientist"]
    assert _query_terms("C++ / C#") == ["c", "c"]
    assert _query_terms("+++") == []


def test_terms_keep_non_ascii_letters() -> None:
    # ESCO alt labels are not all ASCII; splitting on [a-z0-9] would shred them.
    assert _query_terms("ingénieur systèmes") == ["ingénieur", "systèmes"]


def test_phrase_escapes_quotes_and_backslashes() -> None:
    assert _lucene_phrase('say "hi"') == '"say \\"hi\\""'
    assert _lucene_phrase("back\\slash") == '"back\\\\slash"'


def test_phrase_declines_when_nothing_is_indexable() -> None:
    # No terms means the index cannot answer; the caller must scan instead of
    # concluding "not found".
    assert _lucene_phrase("+++") is None
    assert _lucene_phrase("   ") is None


def test_infix_requires_every_term_and_wraps_both_sides() -> None:
    # Infix, not prefix: CONTAINS("metadata science", "data scien") is true.
    assert _lucene_infix("data scien") == "+*data* +*scien*"


def test_infix_declines_short_terms() -> None:
    # A one-letter wildcard expands over most of the term dictionary.
    assert _lucene_infix("C++") is None
    assert _lucene_infix("go dev") is None


def test_exact_pref_cypher_matches_concrete_labels() -> None:
    cypher = _exact_pref_cypher([LABEL_OCCUPATION, LABEL_SKILL])
    assert "MATCH (n:Occupation)" in cypher
    assert "MATCH (n:Skill)" in cypher
    # The umbrella label plus a labels() *filter* is what hid the index.
    # labels(n) still appears in the projection, which costs nothing.
    assert "EscoNode" not in cypher
    assert "any(x IN labels(n)" not in cypher
    assert cypher.count("UNION") == 1


def test_exact_pref_cypher_refuses_labels_outside_the_suite() -> None:
    with pytest.raises(ValueError, match="not searchable"):
        _exact_pref_cypher(["Occupation) DETACH DELETE (n"])


def _row(node_id: str, label: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "pref_label": label,
        "source": "esco",
        "source_id": f"https://example.test/{node_id}",
        "kind": "Occupation",
        "labels": ["EscoNode", "Occupation"],
    }


def test_result_reports_how_many_matches_it_cut() -> None:
    rows = [_row(f"esco:occupation:{i}", f"label {i}") for i in range(SEARCH_LIMIT)]

    result = _locate_result(rows, 900, CONF_CONTAINS, "contains", "contains:x", [])

    assert result.pruning == PruningStats(considered=900, returned=SEARCH_LIMIT, pruned=875)
    assert "truncated" in result.warnings
    assert result.meta == {"limit": SEARCH_LIMIT, "matches": 900}


def test_result_does_not_claim_truncation_when_nothing_was_cut() -> None:
    result = _locate_result([_row("esco:occupation:a", "a")], 1, 0.95, "exact_pref", "x", [])

    assert result.warnings == []
    assert result.pruning == PruningStats(considered=1, returned=1, pruned=0)
