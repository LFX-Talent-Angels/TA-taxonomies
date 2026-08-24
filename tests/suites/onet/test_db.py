"""Unit tests for explicit, suite-scoped Neo4j configuration."""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.onet import db


def test_password_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "load_dotenv", lambda: None)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.delenv("ONET_NEO4J_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="NEO4J_PASSWORD"):
        db.neo4j_config_from_env()


def test_suite_scoped_variables_win(monkeypatch: pytest.MonkeyPatch) -> None:
    # The point of the override: a loader that starts by deleting nodes must be
    # pinnable to its own instance without editing the shared NEO4J_URI that
    # every other suite reads.
    monkeypatch.setattr(db, "load_dotenv", lambda: None)
    monkeypatch.setenv("NEO4J_URI", "bolt://shared:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "shared")
    monkeypatch.setenv("ONET_NEO4J_URI", "bolt://mine:7691")
    monkeypatch.setenv("ONET_NEO4J_PASSWORD", "mine")

    cfg = db.neo4j_config_from_env()

    assert cfg["uri"] == "bolt://mine:7691"
    assert cfg["password"] == "mine"


def test_shared_variables_are_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "load_dotenv", lambda: None)
    monkeypatch.delenv("ONET_NEO4J_URI", raising=False)
    monkeypatch.delenv("ONET_NEO4J_PASSWORD", raising=False)
    monkeypatch.setenv("NEO4J_URI", "bolt://shared:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "shared")

    cfg = db.neo4j_config_from_env()

    assert cfg["uri"] == "bolt://shared:7687"
    assert cfg["password"] == "shared"
