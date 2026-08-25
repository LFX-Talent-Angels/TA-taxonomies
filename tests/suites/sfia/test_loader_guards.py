"""Unit tests for loader isolation and explicit failure behavior."""

from __future__ import annotations

from typing import Any

import pytest

from ta_taxonomies.suites.sfia.load import (
    SfiaLoadValidationError,
    _merge_rel_count,
    main,
    wipe_sfia_graph,
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


def test_wipe_is_scoped_to_the_suite_label_and_source() -> None:
    # The canonical labels (:Skill, :Level) are shared with every other suite in
    # the same graph, so wiping by those would delete ESCO's or O*NET's nodes.
    session = _Session()

    wipe_sfia_graph(session)  # type: ignore[arg-type]

    query, parameters = session.calls[0]
    assert ":SfiaNode" in query
    assert ":Skill" not in query
    assert ":Level" not in query
    assert "n.source = $source" in query
    assert parameters == {"source": "sfia"}


def test_relationship_merge_names_missing_endpoints() -> None:
    session = _Session(count=1)
    rows = [
        {"from_id": "sfia:skill:PROG", "to_id": "sfia:level:4"},
        {"from_id": "sfia:skill:ZZZZ", "to_id": "sfia:level:4"},
    ]

    with pytest.raises(
        SfiaLoadValidationError,
        match="HAS_LEVEL.*attempted 2.*matched 1.*missing endpoints 1",
    ):
        _merge_rel_count(  # type: ignore[arg-type]
            session,
            "UNWIND $rows AS row RETURN 1 AS c",
            rows,
            relationship="HAS_LEVEL",
        )


def test_mode_has_no_default() -> None:
    # A load begins by deleting this suite's nodes. Running the module with no
    # arguments must not be enough to do that.
    with pytest.raises(SystemExit):
        main([])
