"""Neo4j connection helpers for the SFIA suite (Docker or Aura).

Use case: open a Bolt driver from environment variables (``.env`` /
``NEO4J_URI``, user, password, database), yield it safely, and close it.
Used by both the loader (writes) and SfiaSuite tools (reads).

Why it exists: backend choice is configuration only — local
``bolt://localhost:7687`` vs Aura ``neo4j+s://…`` — without branching load or
tool code. No SFIA business logic here. Same pattern as esco/db.py, kept as
the suite's own copy because suites never import each other.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase


def neo4j_config_from_env() -> dict[str, str]:
    load_dotenv()
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        raise ValueError(
            "NEO4J_PASSWORD is required; set it explicitly in the environment or a local .env file"
        )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    return {"uri": uri, "user": user, "password": password, "database": database}


# @contextmanager: enables `with neo4j_driver() as (driver, db):` and always
# runs driver.close() in the finally block (even if the body raises).
@contextmanager
def neo4j_driver() -> Iterator[tuple[Driver, str]]:
    """Open a Neo4j driver from env; close it when the ``with`` block ends.

    Example::

        with neo4j_driver() as (driver, database):
            suite = SfiaSuite(driver, database=database)
            suite.search_nodes("PROG")
    """
    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    try:
        yield driver, cfg["database"]
    finally:
        driver.close()


def verify_connectivity(driver: Driver) -> None:
    driver.verify_connectivity()
