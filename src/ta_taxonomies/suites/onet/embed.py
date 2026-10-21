"""Embed O*NET node pref_labels into Neo4j (`onet_label_embedding`).

Run once after the O*NET graph is loaded, then again after any bulk re-load;
idempotent unless ``--force``. The job itself is shared: see ``suites/_embed.py``.

Usage::

    uv run python -m ta_taxonomies.suites.onet.embed
    uv run python -m ta_taxonomies.suites.onet.embed --force   # re-embed all
"""

from __future__ import annotations

from typing import Any

from ta_taxonomies.suites import _embed

BATCH_SIZE = _embed.BATCH_SIZE
JOB = _embed.EmbedJob(
    vector_index="onet_label_embedding",
    node_label="OnetNode",
    source="onet",
    display_name="O*NET",
)


def create_vector_index(driver: Any) -> None:
    """Create the O*NET vector index if it does not already exist."""
    _embed.create_vector_index(JOB, driver)


def build_embeddings(driver: Any, *, force: bool = False) -> int:
    """Embed every O*NET node's pref_label; returns the number of nodes updated."""
    return _embed.build_embeddings(JOB, driver, force=force)


def main() -> None:
    _embed.main(JOB)


if __name__ == "__main__":
    main()
