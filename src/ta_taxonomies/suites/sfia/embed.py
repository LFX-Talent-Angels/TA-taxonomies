"""Embed SFIA node pref_labels into Neo4j (`sfia_label_embedding`).

Run once after the SFIA graph is loaded, then again after any bulk re-load;
idempotent unless ``--force``. The job itself is shared: see ``suites/_embed.py``.

Skill levels are skipped: each is named after its skill ("Programming/software
development (level 4)"), so its vector would duplicate the skill's, and levels
are left out of default search anyway.

Usage::

    uv run python -m ta_taxonomies.suites.sfia.embed
    uv run python -m ta_taxonomies.suites.sfia.embed --force   # re-embed all
"""

from __future__ import annotations

from typing import Any

from ta_taxonomies.suites import _embed
from ta_taxonomies.suites.sfia.config import LABEL_SFIA_NODE, LABEL_SKILL_LEVEL, SOURCE

BATCH_SIZE = _embed.BATCH_SIZE
JOB = _embed.EmbedJob(
    vector_index="sfia_label_embedding",
    node_label=LABEL_SFIA_NODE,
    source=SOURCE,
    display_name="SFIA",
    skip_labels=(LABEL_SKILL_LEVEL,),
)


def create_vector_index(driver: Any) -> None:
    """Create the SFIA vector index if it does not already exist."""
    _embed.create_vector_index(JOB, driver)


def build_embeddings(driver: Any, *, force: bool = False) -> int:
    """Embed every SFIA node's pref_label; returns the number of nodes updated."""
    return _embed.build_embeddings(JOB, driver, force=force)


def main() -> None:
    _embed.main(JOB)


if __name__ == "__main__":
    main()
