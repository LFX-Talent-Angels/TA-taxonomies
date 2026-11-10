"""One-time batch job: compute pref_label embeddings and write to Neo4j.

Run once after the ESCO graph is loaded, then again after any bulk re-load.
The job is idempotent: nodes that already have a ``label_embedding`` property
are skipped unless ``--force`` is passed.

Usage::

    uv run python -m ta_taxonomies.suites.esco.embed
    uv run python -m ta_taxonomies.suites.esco.embed --force   # re-embed all

Requires the ``embed`` optional dependency group::

    uv pip install -e ".[embed]"

Why all-MiniLM-L6-v2: 384 dimensions, Apache-2.0 license, ~80 MB on disk,
~5 ms/query on CPU. Fast enough for interactive use; small enough to run in CI
if needed (though the index build itself is a one-time job).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

EMBED_MODEL = "all-MiniLM-L6-v2"
VECTOR_DIMS = 384
VECTOR_INDEX = "esco_label_embedding"
NODE_LABEL = "EscoNode"
BATCH_SIZE = 512
SOURCE = "esco"

_FETCH_CYPHER = f"""
MATCH (n:{NODE_LABEL})
WHERE n.source = $source
  AND ($force OR n.label_embedding IS NULL)
  AND ($after IS NULL OR n.id > $after)
RETURN n.id AS id, n.pref_label AS pref_label
ORDER BY n.id
LIMIT $batch
"""

_WRITE_CYPHER = f"""
UNWIND $rows AS row
MATCH (n:{NODE_LABEL} {{id: row.id, source: $source}})
SET n.label_embedding = row.embedding
"""

_CREATE_INDEX_CYPHER = f"""
CREATE VECTOR INDEX {VECTOR_INDEX} IF NOT EXISTS
FOR (n:{NODE_LABEL}) ON n.label_embedding
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {VECTOR_DIMS},
    `vector.similarity_function`: 'cosine'
}}}}
"""


def create_vector_index(driver: Any) -> None:
    """Create the Neo4j vector index if it does not already exist."""
    with driver.session() as session:
        session.run(_CREATE_INDEX_CYPHER)
    print(f"Vector index '{VECTOR_INDEX}' ensured.")


def build_embeddings(driver: Any, *, force: bool = False) -> int:
    """Embed all ESCO node pref_labels and write them back to Neo4j.

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
                _FETCH_CYPHER,
                source=SOURCE,
                force=force,
                after=after,
                batch=BATCH_SIZE,
            ).data()
            if not records:
                break
            labels = [r["pref_label"] or "" for r in records]
            embeddings = model.encode(labels, normalize_embeddings=True)
            rows = [
                {"id": r["id"], "embedding": emb.tolist()}
                for r, emb in zip(records, embeddings, strict=True)
            ]
            session.run(_WRITE_CYPHER, rows=rows, source=SOURCE)
            updated += len(rows)
            after = records[-1]["id"]
            print(f"  embedded {updated} nodes...", end="\r", flush=True)

    print(f"\nDone. Embedded {updated} ESCO nodes.")
    return updated


def _build_driver() -> Any:
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(uri, auth=(user, password))


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed ESCO node labels into Neo4j.")
    parser.add_argument(
        "--force", action="store_true", help="Re-embed even if embedding already exists."
    )
    args = parser.parse_args()

    driver = _build_driver()
    try:
        create_vector_index(driver)
        build_embeddings(driver, force=args.force)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
