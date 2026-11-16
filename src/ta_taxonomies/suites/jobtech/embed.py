"""Embed JobTech node pref_labels into Neo4j (`jobtech_label_embedding`).

Run once after the JobTech graph is loaded; idempotent unless ``--force``. The
job itself is shared: see ``suites/_embed.py``.

Note: the shared model (all-MiniLM-L6-v2) is English. JobTech labels are
Swedish, so these vectors help Swedish-to-Swedish paraphrases only a little
and English questions hardly at all; a multilingual model is the real fix
(see NOTES.md, "Known limits").

Usage::

    uv run python -m ta_taxonomies.suites.jobtech.embed
"""

from __future__ import annotations

from typing import Any

from ta_taxonomies.suites import _embed
from ta_taxonomies.suites.jobtech.config import LABEL_JOBTECH_NODE, SOURCE, VECTOR_INDEX

BATCH_SIZE = _embed.BATCH_SIZE
JOB = _embed.EmbedJob(
    vector_index=VECTOR_INDEX,
    node_label=LABEL_JOBTECH_NODE,
    source=SOURCE,
    display_name="JobTech",
)


def create_vector_index(driver: Any) -> None:
    """Create the JobTech vector index if it does not already exist."""
    _embed.create_vector_index(JOB, driver)


def build_embeddings(driver: Any, *, force: bool = False) -> int:
    """Embed every JobTech node's pref_label; returns the number of nodes updated."""
    return _embed.build_embeddings(JOB, driver, force=force)


def main() -> None:
    _embed.main(JOB)


if __name__ == "__main__":
    main()
