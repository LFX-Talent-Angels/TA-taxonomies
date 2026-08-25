"""Neo4j connection helper for the crosswalk layer.

Use case: open a Bolt driver from the same ``NEO4J_*`` environment variables
every suite uses, so the crosswalk loader and queries reach whatever graph the
suites were loaded into.

Why it exists rather than importing ``suites.esco.db``: ARCHITECTURE.md says
suites never import each other, and the crosswalk layer sits above all of them
-- reaching into one suite for a connection helper would make ESCO a
dependency of every cross-taxonomy query, including ones ESCO has no part in.
The duplication is four lines and buys that independence.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase


def neo4j_config_from_env() -> dict[str, str]:
    load_dotenv()
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        raise ValueError(
            "NEO4J_PASSWORD is required; set it explicitly in the environment or a local .env file"
        )
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": password,
        "database": os.getenv("NEO4J_DATABASE", "neo4j"),
    }


@contextmanager
def neo4j_driver() -> Iterator[tuple[Driver, str]]:
    """Open a driver from env; close it when the ``with`` block ends."""
    config = neo4j_config_from_env()
    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))
    try:
        yield driver, config["database"]
    finally:
        driver.close()


def verify_connectivity(driver: Driver) -> None:
    driver.verify_connectivity()
