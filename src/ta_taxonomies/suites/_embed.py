"""Shared batch job: compute pref_label embeddings and write them to Neo4j.

Each suite's ``embed`` module is a thin wrapper that names its index, node
label and source. Run once after a suite's graph is loaded, then again after
any bulk re-load. The job is idempotent: nodes that already have a
``label_embedding`` property are skipped unless ``--force`` is passed.

Requires the ``embed`` optional dependency group::

    uv pip install -e ".[embed]"

Why all-MiniLM-L6-v2: 384 dimensions, Apache-2.0 license, ~80 MB on disk,
~5 ms/query on CPU. Fast enough for interactive use; small enough to run in CI
if needed (though the index build itself is a one-time job). It must match the
query model in ``_locate.embedding_model``, or the vectors are not comparable.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

EMBED_MODEL = "all-MiniLM-L6-v2"
VECTOR_DIMS = 384
BATCH_SIZE = 512


@dataclass(frozen=True)
class EmbedJob:
    """What one suite embeds: its index, anchor label, source and display name."""

    vector_index: str
    node_label: str
    source: str
    display_name: str
    #: Labels left out, e.g. SFIA's SkillLevel, whose names repeat their skill's.
    skip_labels: tuple[str, ...] = ()

    @property
    def fetch_cypher(self) -> str:
        return f"""
MATCH (n:{self.node_label})
WHERE n.source = $source
  AND ($force OR n.label_embedding IS NULL)
  AND ($after IS NULL OR n.id > $after)
  AND NONE(l IN labels(n) WHERE l IN $skip_labels)
RETURN n.id AS id, n.pref_label AS pref_label
ORDER BY n.id
LIMIT $batch
"""

    @property
    def write_cypher(self) -> str:
        return f"""
UNWIND $rows AS row
MATCH (n:{self.node_label} {{id: row.id, source: $source}})
SET n.label_embedding = row.embedding
"""

    @property
    def create_index_cypher(self) -> str:
        return f"""
CREATE VECTOR INDEX {self.vector_index} IF NOT EXISTS
FOR (n:{self.node_label}) ON n.label_embedding
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {VECTOR_DIMS},
    `vector.similarity_function`: 'cosine'
}}}}
"""


def create_vector_index(job: EmbedJob, driver: Any) -> None:
    """Create the Neo4j vector index if it does not already exist."""
    with driver.session() as session:
        session.run(job.create_index_cypher)
    print(f"Vector index '{job.vector_index}' ensured.")


def build_embeddings(
    job: EmbedJob, driver: Any, *, force: bool = False, batch_size: int = BATCH_SIZE
) -> int:
    """Embed every node's pref_label and write it back to Neo4j.

    Returns the number of nodes updated.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "sentence-transformers not installed. Run: uv pip install -e '.[embed]'",
            file=sys.stderr,
        )
        sys.exit(1)

    model = SentenceTransformer(EMBED_MODEL)
    updated = 0
    # Keyset pagination: resume after the last id written. SKIP/offset paging
    # is wrong here because the IS NULL filter shrinks the result set as each
    # batch is written, so an advancing offset skips half the unembedded nodes.
    after: str | None = None

    with driver.session() as session:
        while True:
            records = session.run(
                job.fetch_cypher,
                source=job.source,
                force=force,
                after=after,
                skip_labels=list(job.skip_labels),
                batch=batch_size,
            ).data()
            if not records:
                break
            labels = [r["pref_label"] or "" for r in records]
            embeddings = model.encode(labels, normalize_embeddings=True)
            rows = [
                {"id": r["id"], "embedding": emb.tolist()}
                for r, emb in zip(records, embeddings, strict=True)
            ]
            session.run(job.write_cypher, rows=rows, source=job.source)
            updated += len(rows)
            after = records[-1]["id"]
            print(f"  embedded {updated} nodes...", end="\r", flush=True)

    print(f"\nDone. Embedded {updated} {job.display_name} nodes.")
    return updated


def _build_driver() -> Any:
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(uri, auth=(user, password))


def main(job: EmbedJob) -> None:
    parser = argparse.ArgumentParser(
        description=f"Embed {job.display_name} node labels into Neo4j."
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-embed even if embedding already exists."
    )
    args = parser.parse_args()

    driver = _build_driver()
    try:
        create_vector_index(job, driver)
        build_embeddings(job, driver, force=args.force)
    finally:
        driver.close()
