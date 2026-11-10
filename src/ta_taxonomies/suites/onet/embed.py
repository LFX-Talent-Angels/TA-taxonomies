"""One-time batch job: compute pref_label embeddings and write to Neo4j.

Mirror of ta_taxonomies.suites.esco.embed for the O*NET graph. Run once
after the O*NET graph is loaded; idempotent (skips nodes that already have
``label_embedding`` unless ``--force``).

Usage::

    uv run python -m ta_taxonomies.suites.onet.embed
    uv run python -m ta_taxonomies.suites.onet.embed --force

Requires the ``embed`` optional dependency group::

    uv pip install -e ".[embed]"
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

EMBED_MODEL = "all-MiniLM-L6-v2"
VECTOR_DIMS = 384
VECTOR_INDEX = "onet_label_embedding"
NODE_LABEL = "OnetNode"
BATCH_SIZE = 512
SOURCE = "onet"

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
    """Embed all O*NET node pref_labels and write them back to Neo4j.

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

    print(f"\nDone. Embedded {updated} O*NET nodes.")
    return updated


def _build_driver() -> Any:
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(uri, auth=(user, password))


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed O*NET node labels into Neo4j.")
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
