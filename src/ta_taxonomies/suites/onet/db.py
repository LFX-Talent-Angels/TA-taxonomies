"""Neo4j connection helpers for the O*NET suite (Docker or Aura).

Same env vars as ESCO (``NEO4J_URI``, user, password, database) so both
suites share ``ta-neo4j``. No O*NET business logic here.
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


@contextmanager
def neo4j_driver() -> Iterator[tuple[Driver, str]]:
    """Open a Neo4j driver from env; close it when the ``with`` block ends."""
    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    try:
        yield driver, cfg["database"]
    finally:
        driver.close()


def verify_connectivity(driver: Driver) -> None:
    driver.verify_connectivity()
