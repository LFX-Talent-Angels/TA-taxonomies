"""Skip guard for BLS tests that need a live Neo4j.

These tests reload the fixture with ``wipe=True``, so they delete this suite's
nodes in whatever database ``BLS_NEO4J_URI`` / ``NEO4J_URI`` points at. The
suite-scoped variable exists so that can be a throwaway instance — see
``suites/bls/NOTES.md``.

Registering the marker here rather than in ``pyproject.toml`` keeps the whole
mechanism inside this suite's own tests.
"""

from __future__ import annotations

import os

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable

from ta_taxonomies.suites.bls.db import neo4j_driver, verify_connectivity

SKIP_REASON = "Neo4j not available (see src/ta_taxonomies/suites/bls/NOTES.md)"


def neo4j_available() -> bool:
    if not (os.getenv("BLS_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD")):
        return False
    try:
        with neo4j_driver() as (driver, _db):
            verify_connectivity(driver)
        return True
    except (ServiceUnavailable, AuthError, OSError):
        return False


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "neo4j: requires a live Neo4j instance")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if neo4j_available():
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if "neo4j" in item.keywords:
            item.add_marker(skip)
