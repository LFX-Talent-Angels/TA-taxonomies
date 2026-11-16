"""Embed BLS node pref_labels into Neo4j (`bls_label_embedding`).

Run once after the BLS graph is loaded, then again after any bulk re-load;
idempotent unless ``--force``. The job itself is shared: see ``suites/_embed.py``.

Education and training levels are skipped: they are a handful of category
names ("Bachelor's degree") that keyword search already finds exactly.

Usage::

    uv run python -m ta_taxonomies.suites.bls.embed
    uv run python -m ta_taxonomies.suites.bls.embed --force   # re-embed all
"""

from __future__ import annotations

from typing import Any

from ta_taxonomies.suites import _embed
from ta_taxonomies.suites.bls.config import (
    LABEL_BLS_NODE,
    LABEL_EDUCATION,
    LABEL_TRAINING,
    SOURCE,
    VECTOR_INDEX,
)

BATCH_SIZE = _embed.BATCH_SIZE
JOB = _embed.EmbedJob(
    vector_index=VECTOR_INDEX,
    node_label=LABEL_BLS_NODE,
    source=SOURCE,
    display_name="BLS",
    skip_labels=(LABEL_EDUCATION, LABEL_TRAINING),
)


def create_vector_index(driver: Any) -> None:
    """Create the BLS vector index if it does not already exist."""
    _embed.create_vector_index(JOB, driver)


def build_embeddings(driver: Any, *, force: bool = False) -> int:
    """Embed every BLS node's pref_label; returns the number of nodes updated."""
    return _embed.build_embeddings(JOB, driver, force=force)


def main() -> None:
    _embed.main(JOB)


if __name__ == "__main__":
    main()
