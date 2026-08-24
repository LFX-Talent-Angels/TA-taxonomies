"""Neo4j connection helpers for the O*NET suite (Docker or Aura).

Use case: open a Bolt driver from environment variables, yield it safely, and
close it. Used by both the loader (writes) and OnetSuite tools (reads).

Why the ``ONET_NEO4J_*`` overrides exist: loading a suite starts by deleting
that suite's nodes, and every suite reads the same ``NEO4J_URI`` by default.
One stale export is enough to point a load at the graph someone was using.
The suite-scoped variables let a developer pin this loader to its own instance
without editing the shared ones, and the resolved URI is printed at load time
so the target is visible before anything is deleted rather than after.

This duplicates ``suites/esco/db.py`` almost exactly. That is deliberate under
the "suites never import each other" rule; the shared version belongs in
``ingestion/`` and moving ESCO's copy there is a change to another suite's
owner's code, so it is left for a PR that can touch both.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase


def neo4j_config_from_env() -> dict[str, str]:
    """Resolve connection settings, preferring ``ONET_NEO4J_*`` over ``NEO4J_*``."""
    load_dotenv()

    def pick(name: str, default: str | None = None) -> str | None:
        return os.getenv(f"ONET_{name}") or os.getenv(name) or default

    uri = pick("NEO4J_URI", "bolt://localhost:7687")
    user = pick("NEO4J_USER", "neo4j")
    password = pick("NEO4J_PASSWORD")
    if not password:
        raise ValueError(
            "NEO4J_PASSWORD (or ONET_NEO4J_PASSWORD) is required; "
            "set it explicitly in the environment or a local .env file"
        )
    database = pick("NEO4J_DATABASE", "neo4j")
    # mypy: pick() returns str | None, but each of these has a default or was
    # checked above.
    return {
        "uri": str(uri),
        "user": str(user),
        "password": password,
        "database": str(database),
    }


@contextmanager
def neo4j_driver() -> Iterator[tuple[Driver, str]]:
    """Open a Neo4j driver from env; close it when the ``with`` block ends.

    Example::

        with neo4j_driver() as (driver, database):
            suite = OnetSuite(driver, database=database)
            suite.search_nodes("software developers")
    """
    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    try:
        yield driver, cfg["database"]
    finally:
        driver.close()


def verify_connectivity(driver: Driver) -> None:
    driver.verify_connectivity()
