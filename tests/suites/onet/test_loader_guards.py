"""Unit tests for loader isolation and explicit failure behavior."""

from __future__ import annotations

from typing import Any

import pytest

from ta_taxonomies.suites.onet.load import (
    OnetLoadValidationError,
    _merge_rel_count,
    main,
    wipe_onet_graph,
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
    # The canonical labels (:Occupation, :Skill) are shared with every other
    # suite in the same graph, so wiping by those would delete ESCO's nodes.
    session = _Session()

    wipe_onet_graph(session)  # type: ignore[arg-type]

    query, parameters = session.calls[0]
    assert ":OnetNode" in query
    assert "Occupation" not in query
    assert "n.source = $source" in query
    assert parameters == {"source": "onet"}


def test_relationship_merge_names_missing_endpoints() -> None:
    session = _Session(count=1)
    rows = [
        {"from_id": "onet:occupation:15-1252.00", "to_id": "onet:element:2.B.3.e"},
        {"from_id": "onet:occupation:15-1251.00", "to_id": "onet:element:9.Z.9.z"},
    ]

    with pytest.raises(
        OnetLoadValidationError,
        match="HAS_SKILL.*attempted 2.*matched 1.*missing endpoints 1",
    ):
        _merge_rel_count(  # type: ignore[arg-type]
            session,
            "UNWIND $rows AS row RETURN 1 AS c",
            rows,
            relationship="HAS_SKILL",
        )


def test_mode_has_no_default() -> None:
    # A load begins by deleting this suite's nodes. Running the module with no
    # arguments must not be enough to do that.
    with pytest.raises(SystemExit):
        main([])
