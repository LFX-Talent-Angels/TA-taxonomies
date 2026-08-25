"""Loader isolation, schema scoping and explicit failure behaviour. No Neo4j.

The schema tests here are not decoration. ``suites/onet/NOTES.md`` measured
that a second uniqueness constraint on a label another suite already constrains
is a **silent no-op** under ``IF NOT EXISTS`` — it does not fail, it just never
exists, and the suite then depends on a schema object it does not own. Nothing
at query time would ever say so, which is why it is checked in the statements
rather than trusted from the comment above them.
"""

from __future__ import annotations

from typing import Any

import pytest

from ta_taxonomies.suites.bls import config
from ta_taxonomies.suites.bls.load import (
    BlsLoadValidationError,
    _merge_rel_count,
    wipe_bls_graph,
)
from ta_taxonomies.suites.bls.schema import (
    ALL_STATEMENTS,
    CODE_INDEXES,
    CONSTRAINTS,
    FULLTEXT_INDEXES,
)


class _Result:
    def __init__(self, record: dict[str, Any] | None = None) -> None:
        self._record = record

    def single(self) -> dict[str, Any] | None:
        return self._record


class _Session:
    def __init__(self, count: int = 0) -> None:
        self.count = count
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> _Result:
        self.calls.append((query, parameters))
        return _Result({"c": self.count})


# --- isolation -------------------------------------------------------------


def test_the_wipe_is_scoped_to_this_suite_by_source_and_by_umbrella_label() -> None:
    """The canonical labels are shared; only the umbrella is this suite's."""
    session = _Session()

    wipe_bls_graph(session)  # type: ignore[arg-type]

    query, parameters = session.calls[0]
    assert "n.source = $source" in query
    assert f":{config.LABEL_BLS_NODE}" in query
    assert ":Occupation)" not in query
    assert parameters == {"source": "bls"}


def test_a_relationship_merge_names_its_missing_endpoints() -> None:
    session = _Session(count=1)
    rows = [
        {"from_id": "bls:occupation:15-1252", "to_id": "bls:industry:5415A1"},
        {"from_id": "bls:occupation:15-1252", "to_id": "bls:industry:missing"},
    ]

    with pytest.raises(BlsLoadValidationError, match="missing endpoints 1"):
        _merge_rel_count(
            session,  # type: ignore[arg-type]
            "cypher",
            rows,
            relationship=config.REL_EMPLOYED_IN,
        )


# --- schema scoping --------------------------------------------------------


def test_no_schema_object_is_declared_on_a_shared_canonical_label() -> None:
    """A constraint on :Occupation(id) would silently be ESCO's, or become it.

    Neo4j 5 accepts the duplicate under IF NOT EXISTS and creates nothing, so
    the failure mode is invisible in both directions: this suite would believe
    it owned a constraint it did not, and dropping ESCO's would remove this
    suite's uniqueness guarantee with no message anywhere.
    """
    shared = {value for value in config.CANONICAL_LABELS.values() if value}
    assert shared  # the test is vacuous if the canonical map is empty
    for statement in ALL_STATEMENTS:
        for label in shared:
            assert f"(n:{label})" not in statement, statement
            assert f"(n:{label} " not in statement, statement


def test_every_searchable_label_gets_its_own_uniqueness_constraint() -> None:
    """Derived from the config rather than listed, and the test proves it.

    Asserting "the statements name the right labels" cannot distinguish a
    derived list from a literal that currently agrees with it, so this
    monkeypatch-free version checks the relationship instead: every configured
    label appears, and the count matches.
    """
    for label in config.SEARCHABLE_LABELS:
        assert any(f"(n:{label})" in stmt and "CONSTRAINT" in stmt for stmt in CONSTRAINTS)
    # umbrella + one per searchable label
    assert len(CONSTRAINTS) == 1 + len(config.SEARCHABLE_LABELS)


def test_the_soc_code_is_indexed_because_that_is_how_a_crosswalk_arrives() -> None:
    """A crosswalk holds a code, not a title. Resolving one must be a seek."""
    assert any("ON (n.code)" in stmt for stmt in CODE_INDEXES)
    assert len(CODE_INDEXES) == len(config.SEARCHABLE_LABELS)


def test_the_fulltext_index_ships_with_the_first_load() -> None:
    (statement,) = FULLTEXT_INDEXES
    assert config.FULLTEXT_INDEX in statement
    # 'standard' would strip English stop words, making an exact title such as
    # "Healthcare diagnosing or treating practitioners" unreachable through the
    # index while a scan still found it.
    assert config.FULLTEXT_ANALYZER in statement
    assert "n.pref_label" in statement and "n.alt_labels" in statement
    # The loader validates immediately after MERGE; a lagging index would make
    # those assertions flaky.
    assert "`fulltext.eventually_consistent`: false" in statement


# --- config governs, rather than agreeing ----------------------------------


def test_the_schema_follows_the_configured_label_set_rather_than_agreeing_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A set that cannot vary proves nothing about derivation.

    ``suites/onet/NOTES.md``: replacing a config constant with a hardcoded list
    that happens to say the same thing left all 100 tests green. The only test
    that can tell the difference is one that changes the config and requires
    the output to follow.
    """
    import importlib

    from ta_taxonomies.suites.bls import schema

    monkeypatch.setattr(config, "SEARCHABLE_LABELS", (*config.SEARCHABLE_LABELS, "BlsInvented"))
    reloaded = importlib.reload(schema)
    try:
        assert any("(n:BlsInvented)" in stmt for stmt in reloaded.CONSTRAINTS)
        assert any("BlsInvented" in stmt for stmt in reloaded.FULLTEXT_INDEXES)
    finally:
        monkeypatch.undo()
        importlib.reload(schema)


def test_industries_carry_no_canonical_label_and_that_is_deliberate() -> None:
    """ARCHITECTURE.md's node vocabulary has no kind for the economic dimension.

    Skill · Task · Occupation · Framework · Level · Evidence. An industry is
    none of those, and borrowing the nearest would put BLS industries into
    whatever a cross-suite reader means by :Framework. Recorded as a contract
    gap in NOTES.md rather than papered over.
    """
    assert config.CANONICAL_LABELS[config.LABEL_INDUSTRY] is None
    assert config.CANONICAL_LABELS[config.LABEL_OCCUPATION] == "Occupation"


def test_the_traversable_set_does_not_include_a_skills_edge() -> None:
    """BLS publishes no skills layer, and get_neighbors must say so, not imply it."""
    assert "HAS_SKILL" not in config.TRAVERSABLE_RELS
    assert config.TRAVERSABLE_RELS == frozenset({config.REL_BROADER_THAN, config.REL_EMPLOYED_IN})
