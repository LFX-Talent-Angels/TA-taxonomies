"""BLS loader isolation and explicit failures (fake session, no Neo4j)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ta_taxonomies.suites.bls.load import (
    BlsLoadValidationError,
    _merge_edges,
    load_full_document,
    main,
    wipe_bls_graph,
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

    def run(self, cypher: str, **params: Any) -> _Result:
        self.calls.append((cypher, params))
        return _Result({"c": self.count})


def test_wipe_only_touches_bls_nodes() -> None:
    session = _Session()
    wipe_bls_graph(session)  # type: ignore[arg-type]
    cypher, params = session.calls[0]
    assert "MATCH (n:BlsNode)" in cypher
    assert "n.source = $source" in cypher
    assert params == {"source": "bls"}


def test_missing_endpoints_fail_loudly() -> None:
    rows = [{"from_id": "bls:occupation:15-1252", "to_id": "bls:skill:x", "props": {}}] * 3
    with pytest.raises(BlsLoadValidationError, match="missing endpoints 1"):
        _merge_edges(_Session(count=2), "HAS_SKILL", "Occupation", "Skill", rows)  # type: ignore[arg-type]


def test_edge_merge_matches_both_ends_inside_the_suite() -> None:
    session = _Session(count=1)
    _merge_edges(session, "HAS_SKILL", "Occupation", "Skill", [{"from_id": "a", "to_id": "b"}])  # type: ignore[arg-type]
    cypher = session.calls[0][0]
    assert "(a:Occupation:BlsNode" in cypher
    assert "(b:Skill:BlsNode" in cypher


def test_full_mode_explains_how_to_build_the_document(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="bls.fetch"):
        load_full_document(tmp_path / "missing.json")


def test_main_prints_one_line_for_a_missing_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--mode", "full", "--data-path", str(tmp_path / "missing.json")])
    assert code == 1
    assert capsys.readouterr().err.startswith("error: Missing BLS document")


def test_fixture_is_valid_json_with_the_document_shape() -> None:
    path = Path(__file__).parents[3] / "src/ta_taxonomies/suites/bls/fixtures/fixture.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) == {"meta", "groups", "occupations", "skills"}
