"""Embed ESCO node pref_labels into Neo4j (`esco_label_embedding`).

Run once after the ESCO graph is loaded, then again after any bulk re-load;
idempotent unless ``--force``. The job itself is shared: see ``suites/_embed.py``.

Usage::

    uv run python -m ta_taxonomies.suites.esco.embed
    uv run python -m ta_taxonomies.suites.esco.embed --force   # re-embed all
"""

from __future__ import annotations

from typing import Any

from ta_taxonomies.suites import _embed

BATCH_SIZE = _embed.BATCH_SIZE
JOB = _embed.EmbedJob(
    vector_index="esco_label_embedding",
    node_label="EscoNode",
    source="esco",
    display_name="ESCO",
)


def create_vector_index(driver: Any) -> None:
    """Create the ESCO vector index if it does not already exist."""
    _embed.create_vector_index(JOB, driver)


def build_embeddings(driver: Any, *, force: bool = False) -> int:
    """Embed every ESCO node's pref_label; returns the number of nodes updated."""
    return _embed.build_embeddings(JOB, driver, force=force)


def main() -> None:
    _embed.main(JOB)


if __name__ == "__main__":
    main()
