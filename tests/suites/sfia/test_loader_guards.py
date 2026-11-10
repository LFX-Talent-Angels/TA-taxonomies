"""Unit tests for loader isolation and explicit failure behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ta_taxonomies.suites.sfia.load import (
    SfiaLoadValidationError,
    _merge_rel_count,
    load_full_document,
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


def test_wipe_is_scoped_to_sfia_source() -> None:
    session = _Session()

    wipe_sfia_graph(session)  # type: ignore[arg-type]

    query, parameters = session.calls[0]
    assert "n.source = $source" in query
    assert "SfiaNode" in query
    assert parameters == {"source": "sfia"}


def test_relationship_merge_names_missing_endpoints() -> None:
    session = _Session(count=1)
    rows = [
        {"from_id": "sfia:skill:PROG", "to_id": "sfia:level:PROG:4"},
        {"from_id": "sfia:skill:PROG", "to_id": "sfia:level:PROG:9"},
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


def test_full_mode_reads_sfia_data_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "structure.json"
    path.write_text(json.dumps({"skills": []}), encoding="utf-8")
    monkeypatch.setenv("SFIA_DATA_PATH", str(path))

    assert load_full_document() == {"skills": []}


def test_missing_full_data_says_how_to_fetch_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="sfia.fetch"):
        load_full_document(tmp_path / "missing.json")


def test_main_fails_with_one_line_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--mode", "full", "--data-path", str(tmp_path / "missing.json")])

    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("error: Missing SFIA structure file")
    assert "Traceback" not in err
