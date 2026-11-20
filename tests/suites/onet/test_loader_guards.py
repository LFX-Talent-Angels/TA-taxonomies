"""Loader isolation and explicit failure behavior."""

from __future__ import annotations

from typing import Any

import pytest

from ta_taxonomies.suites.onet.load import (
    OnetLoadValidationError,
    _merge_rel_count,
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


def test_wipe_is_scoped_to_onet_node_and_source() -> None:
    session = _Session()
    wipe_onet_graph(session)  # type: ignore[arg-type]
    query, parameters = session.calls[0]
    assert "OnetNode" in query
    assert "n.source = $source" in query
    assert parameters == {"source": "onet"}
    assert "DETACH DELETE n" in query
    assert "MATCH (n) DETACH DELETE n" not in query.replace(" ", "")


def test_relationship_merge_names_missing_endpoints() -> None:
    session = _Session(count=1)
    rows = [
        {"from_id": "onet:occupation:a", "to_id": "onet:element:a"},
        {"from_id": "onet:occupation:b", "to_id": "onet:element:missing"},
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
