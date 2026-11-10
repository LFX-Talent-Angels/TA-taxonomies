"""SFIA graph loader: build the suite knowledge graph in Neo4j.

Use case: reproducible ingestion for CI (small committed fixture) or the full
SFIA 9 structure on a developer machine / Aura. Pipeline is
read → normalize → MERGE → validate. Does not implement Locate/Connect;
that is tools.py after the graph exists.

Entrypoint::

    python -m ta_taxonomies.suites.sfia.load --mode fixture
    python -m ta_taxonomies.suites.sfia.load --mode full

Both modes read the same JSON document shape (``fetch.py`` writes it from the
public "skills at a glance" page; the fixture is a hand-checked subset of it),
so one ``normalize_document`` serves both.

Licensing: SFIA prose is under a fee-bearing redistribution license, so the
graph holds structure only — codes, names, level numbers, category slugs — and
each node points at its source with ``sfia_ref`` (and skills with ``url``).
Store the pointer, fetch the payload at runtime. Why it exists: one path for
fixture and full data, pointer-not-payload (the structure dump stays
gitignored), and post-load assertions (counts, no dangling edges, every level
belongs to its skill). Target DB is whatever NEO4J_* env points at.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from neo4j import Driver, Session
from neo4j.exceptions import AuthError, ServiceUnavailable

from ta_taxonomies.suites.sfia.config import (
    FRAMEWORK,
    LABEL_CATEGORY,
    LABEL_SFIA_NODE,
    LABEL_SKILL,
    LABEL_SKILL_LEVEL,
    LABEL_SUBCATEGORY,
    REL_BROADER_THAN,
    REL_HAS_LEVEL,
    SOURCE,
)
from ta_taxonomies.suites.sfia.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.sfia.ids import (
    category_id,
    code_to_str,
    level_id,
    level_ref,
    level_source_id,
    level_to_int,
    skill_id,
    skill_ref,
    skill_url,
    slugify,
    subcategory_id,
)
from ta_taxonomies.suites.sfia.schema import apply_schema

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fixture.json"
DEFAULT_DATA_PATH = "data/sfia/sfia9_structure.json"
BATCH = 200

# Node buckets in load order (parents first, so edges always find endpoints).
NODE_KEYS: tuple[tuple[str, str], ...] = (
    ("categories", LABEL_CATEGORY),
    ("subcategories", LABEL_SUBCATEGORY),
    ("skills", LABEL_SKILL),
    ("skill_levels", LABEL_SKILL_LEVEL),
)


class SfiaLoadValidationError(RuntimeError):
    """Raised when normalized SFIA rows cannot be represented faithfully."""


def _fixture_path() -> Path:
    return FIXTURE_PATH


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    p = path or _fixture_path()
    return json.loads(p.read_text(encoding="utf-8"))


def load_full_document(path: Path | None = None) -> dict[str, Any]:
    """Read the full SFIA structure JSON (``SFIA_DATA_PATH``, written by fetch.py)."""
    p = path or Path(os.getenv("SFIA_DATA_PATH", DEFAULT_DATA_PATH))
    if not p.exists():
        raise FileNotFoundError(
            f"Missing SFIA structure file: {p}. Create it with: "
            f"python -m ta_taxonomies.suites.sfia.fetch --out {p}"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def _merge_nodes(session: Session, label: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    # No description / alt_labels: SFIA has no aliases and its prose is licensed.
    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{label} {{id: row.id}})
    SET n:{LABEL_SFIA_NODE}
    SET n.source = row.source,
        n.source_id = row.source_id,
        n.pref_label = row.pref_label,
        n.code = row.code,
        n.kind = row.kind,
        n.sfia_ref = row.sfia_ref
    SET n += row.extra
    """
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        session.run(cypher, rows=chunk)
        total += len(chunk)
    return total


def _merge_rel_count(
    session: Session,
    cypher: str,
    rows: list[dict[str, Any]],
    *,
    relationship: str,
) -> int:
    """Run batched MERGE, failing clearly when any endpoint is missing."""
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        rec = session.run(cypher, rows=chunk).single()
        matched = int(rec["c"]) if rec else 0
        if matched != len(chunk):
            raise SfiaLoadValidationError(
                f"{relationship} merge attempted {len(chunk)} rows but matched {matched}; "
                f"missing endpoints {len(chunk) - matched}"
            )
        total += matched
    return total


def _merge_has_level(session: Session, rows: list[dict[str, Any]]) -> int:
    cypher = f"""
    UNWIND $rows AS row
    MATCH (s:{LABEL_SKILL}:{LABEL_SFIA_NODE} {{id: row.from_id}})
    MATCH (l:{LABEL_SKILL_LEVEL} {{id: row.to_id}})
    MERGE (s)-[:{REL_HAS_LEVEL}]->(l)
    RETURN count(*) AS c
    """
    return _merge_rel_count(session, cypher, rows, relationship=REL_HAS_LEVEL)


def _merge_broader(session: Session, rows: list[dict[str, Any]]) -> int:
    """MERGE BROADER_THAN edges. Endpoints may be different labels; match by id."""
    cypher = f"""
    UNWIND $rows AS row
    MATCH (a:{LABEL_SFIA_NODE} {{id: row.from_id}})
    MATCH (b:{LABEL_SFIA_NODE} {{id: row.to_id}})
    MERGE (a)-[:{REL_BROADER_THAN}]->(b)
    RETURN count(*) AS c
    """
    return _merge_rel_count(session, cypher, rows, relationship=REL_BROADER_THAN)


def _node_row(
    *,
    node_id: str,
    source_id: str,
    label_kind: str,
    pref_label: str,
    code: str | None = None,
    sfia_ref: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "source": SOURCE,
        "source_id": source_id,
        "pref_label": pref_label or "",
        "code": code,
        "kind": label_kind,
        "sfia_ref": sfia_ref,
        "extra": dict(extra or {}),
    }


def _slug(row: Mapping[str, Any], name_key: str = "name", slug_key: str = "slug") -> str:
    return slugify(str(row.get(slug_key) or row.get(name_key) or ""))


def normalize_document(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Translate an SFIA structure document (fixture or full) into MERGE payloads.

    Reads only structure (names, slugs, codes, level numbers). Any other key a
    document may carry is ignored, so no SFIA prose can reach the graph.
    """
    categories: list[dict[str, Any]] = []
    cat_by_name: dict[str, str] = {}
    for c in doc.get("categories", []):
        slug = _slug(c)
        row = _node_row(
            node_id=category_id(slug),
            source_id=f"cat:{slug}",
            label_kind=LABEL_CATEGORY,
            pref_label=str(c.get("name") or ""),
            sfia_ref=f"{FRAMEWORK} · category {c.get('name')}",
            extra={"slug": slug},
        )
        categories.append(row)
        cat_by_name[row["pref_label"]] = row["id"]

    subcategories: list[dict[str, Any]] = []
    broader: list[dict[str, Any]] = []
    sub_by_name: dict[str, str] = {}
    for index, s in enumerate(doc.get("subcategories", [])):
        slug = _slug(s)
        row = _node_row(
            node_id=subcategory_id(slug),
            source_id=f"sub:{slug}",
            label_kind=LABEL_SUBCATEGORY,
            pref_label=str(s.get("name") or ""),
            sfia_ref=f"{FRAMEWORK} · subcategory {s.get('name')}",
            extra={"slug": slug},
        )
        subcategories.append(row)
        sub_by_name[row["pref_label"]] = row["id"]
        parent = cat_by_name.get(str(s.get("category") or ""))
        if parent is None:
            raise SfiaLoadValidationError(
                f"subcategories row {index} ({row['pref_label']!r}) names unknown category "
                f"{s.get('category')!r}"
            )
        broader.append({"from_id": row["id"], "to_id": parent})

    skills: list[dict[str, Any]] = []
    skill_levels: list[dict[str, Any]] = []
    has_level: list[dict[str, Any]] = []
    for index, k in enumerate(doc.get("skills", [])):
        code = code_to_str(k.get("code"))
        name = str(k.get("name") or "")
        levels = sorted({level_to_int(n) for n in k.get("levels") or []})
        if not levels:
            raise SfiaLoadValidationError(f"skills row {index} ({code}) has no levels")
        sid = skill_id(code)
        skills.append(
            _node_row(
                node_id=sid,
                source_id=code,
                label_kind=LABEL_SKILL,
                pref_label=name,
                code=code,
                sfia_ref=skill_ref(code),
                extra={"url": skill_url(_slug(k)), "levels": levels},
            )
        )
        sub_id = sub_by_name.get(str(k.get("subcategory") or ""))
        if sub_id is None and k.get("subcategory_slug"):
            sub_id = subcategory_id(str(k["subcategory_slug"]))
            if sub_id not in sub_by_name.values():
                sub_id = None
        if sub_id is None:
            raise SfiaLoadValidationError(
                f"skills row {index} ({code}) names unknown subcategory {k.get('subcategory')!r}"
            )
        broader.append({"from_id": sid, "to_id": sub_id})
        for n in levels:
            lid = level_id(code, n)
            skill_levels.append(
                _node_row(
                    node_id=lid,
                    source_id=level_source_id(code, n),
                    label_kind=LABEL_SKILL_LEVEL,
                    pref_label=f"{name} (level {n})",
                    code=level_source_id(code, n),
                    sfia_ref=level_ref(code, n),
                    extra={"level": n, "skill_code": code},
                )
            )
            has_level.append({"from_id": sid, "to_id": lid})

    return {
        "categories": categories,
        "subcategories": subcategories,
        "skills": skills,
        "skill_levels": skill_levels,
        "has_level": has_level,
        "broader": broader,
    }


def wipe_sfia_graph(session: Session) -> None:
    """Delete SFIA suite nodes only (never the whole database / other suites)."""
    session.run(
        f"MATCH (n:{LABEL_SFIA_NODE}) WHERE n.source = $source DETACH DELETE n",
        source=SOURCE,
    )


def load_normalized(
    driver: Driver,
    payload: dict[str, list[dict[str, Any]]],
    *,
    database: str | None = None,
    wipe: bool = True,
) -> dict[str, int]:
    apply_schema(driver, database=database)
    counts: dict[str, int] = {}
    with driver.session(database=database) as session:
        if wipe:
            wipe_sfia_graph(session)
        for key, label in NODE_KEYS:
            print(f"  merging {key.replace('_', ' ')} …", flush=True)
            counts[key] = _merge_nodes(session, label, payload[key])
            print(f"    → {counts[key]:,}", flush=True)
        print("  merging BROADER_THAN …", flush=True)
        counts["broader_than"] = _merge_broader(session, payload["broader"])
        print(f"    → {counts['broader_than']:,}", flush=True)
        print("  merging HAS_LEVEL …", flush=True)
        counts["has_level"] = _merge_has_level(session, payload["has_level"])
        print(f"    → {counts['has_level']:,}", flush=True)
    return counts


def _single_count(session: Session, cypher: str) -> int:
    rec = session.run(cypher, source=SOURCE).single()
    return int(rec["c"]) if rec else 0


def validate_load(
    driver: Driver,
    expected: Mapping[str, int],
    *,
    database: str | None = None,
) -> dict[str, int]:
    """Assert load invariants; return live counts."""
    with driver.session(database=database) as session:

        def count_label(label: str) -> int:
            return _single_count(
                session,
                f"MATCH (n:{label}:{LABEL_SFIA_NODE}) WHERE n.source = $source "
                "RETURN count(n) AS c",
            )

        def count_rel(rel: str) -> int:
            return _single_count(
                session,
                f"MATCH (a:{LABEL_SFIA_NODE})-[r:{rel}]->(b:{LABEL_SFIA_NODE}) "
                "WHERE a.source = $source AND b.source = $source RETURN count(r) AS c",
            )

        live = {
            "categories": count_label(LABEL_CATEGORY),
            "subcategories": count_label(LABEL_SUBCATEGORY),
            "skills": count_label(LABEL_SKILL),
            "skill_levels": count_label(LABEL_SKILL_LEVEL),
            "has_level": count_rel(REL_HAS_LEVEL),
            "broader_than": count_rel(REL_BROADER_THAN),
        }

        checks = {
            # HAS_LEVEL must run Skill -> SkillLevel, nothing else.
            "dangling HAS_LEVEL edges": f"""
                MATCH (a:{LABEL_SFIA_NODE} {{source: $source}})-[r:{REL_HAS_LEVEL}]->(b)
                WHERE NOT a:{LABEL_SKILL} OR NOT b:{LABEL_SKILL_LEVEL}
                   OR coalesce(b.source, '') <> $source
                RETURN count(r) AS c
            """,
            # BROADER_THAN only Skill -> Subcategory and Subcategory -> Category.
            "dangling BROADER_THAN edges": f"""
                MATCH (a:{LABEL_SFIA_NODE} {{source: $source}})-[r:{REL_BROADER_THAN}]->(b)
                WHERE coalesce(b.source, '') <> $source
                   OR NOT ((a:{LABEL_SKILL} AND b:{LABEL_SUBCATEGORY})
                        OR (a:{LABEL_SUBCATEGORY} AND b:{LABEL_CATEGORY}))
                RETURN count(r) AS c
            """,
            "blank identity nodes": f"""
                MATCH (n:{LABEL_SFIA_NODE})
                WHERE n.source = $source
                  AND (n.id IS NULL OR n.id = '' OR n.source_id IS NULL OR n.source_id = ''
                       OR n.pref_label IS NULL OR n.pref_label = '')
                RETURN count(n) AS c
            """,
            "skill levels outside their skill's levels": f"""
                MATCH (s:{LABEL_SKILL}:{LABEL_SFIA_NODE} {{source: $source}})
                      -[:{REL_HAS_LEVEL}]->(l:{LABEL_SKILL_LEVEL})
                WHERE NOT l.level IN s.levels OR l.skill_code <> s.code
                RETURN count(l) AS c
            """,
            "skill levels without exactly one skill": f"""
                MATCH (l:{LABEL_SKILL_LEVEL}:{LABEL_SFIA_NODE} {{source: $source}})
                WHERE COUNT {{ (l)<-[:{REL_HAS_LEVEL}]-() }} <> 1
                RETURN count(l) AS c
            """,
            "skills whose HAS_LEVEL edges differ from their levels": f"""
                MATCH (s:{LABEL_SKILL}:{LABEL_SFIA_NODE} {{source: $source}})
                WHERE COUNT {{ (s)-[:{REL_HAS_LEVEL}]->() }} = 0
                   OR COUNT {{ (s)-[:{REL_HAS_LEVEL}]->() }} <> size(s.levels)
                RETURN count(s) AS c
            """,
            "skills without exactly one subcategory": f"""
                MATCH (s:{LABEL_SKILL}:{LABEL_SFIA_NODE} {{source: $source}})
                WHERE COUNT {{ (s)-[:{REL_BROADER_THAN}]->(:{LABEL_SUBCATEGORY}) }} <> 1
                RETURN count(s) AS c
            """,
            "subcategories without exactly one category": f"""
                MATCH (s:{LABEL_SUBCATEGORY}:{LABEL_SFIA_NODE} {{source: $source}})
                WHERE COUNT {{ (s)-[:{REL_BROADER_THAN}]->(:{LABEL_CATEGORY}) }} <> 1
                RETURN count(s) AS c
            """,
        }
        failures = {name: _single_count(session, cypher) for name, cypher in checks.items()}

    for name, expected_count in expected.items():
        actual_count = live[name]
        if actual_count != expected_count:
            raise SfiaLoadValidationError(
                f"SFIA {name} count mismatch: expected {expected_count}, found {actual_count}"
            )
    for name, count in failures.items():
        if count:
            raise SfiaLoadValidationError(f"{name}: {count}")
    return live


def _dedupe_edges(rows: list[dict[str, Any]], rel_type: str) -> list[dict[str, Any]]:
    """Keep one row per (from_id, to_id, type); last row wins for properties."""
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        seen[(row["from_id"], row["to_id"], rel_type)] = row
    return list(seen.values())


def _dedupe_payload(payload: dict[str, list[dict[str, Any]]]) -> None:
    """Unique nodes by id and edges by (from, to, type), in place."""
    for key, _label in NODE_KEYS:
        seen: dict[str, dict[str, Any]] = {}
        for row in payload[key]:
            seen[row["id"]] = row
        payload[key] = list(seen.values())
    payload["has_level"] = _dedupe_edges(payload["has_level"], REL_HAS_LEVEL)
    payload["broader"] = _dedupe_edges(payload["broader"], REL_BROADER_THAN)


def _document_expectations(doc: Mapping[str, Any]) -> dict[str, int]:
    """Counts the source document itself claims (full mode: 147 / 672 / 22 / 6)."""
    skills = doc.get("skills", [])
    return {
        "categories": len({_slug(c) for c in doc.get("categories", [])}),
        "subcategories": len({_slug(s) for s in doc.get("subcategories", [])}),
        "skills": len({code_to_str(k.get("code")) for k in skills}),
        "skill_levels": len(
            {
                (code_to_str(k.get("code")), level_to_int(n))
                for k in skills
                for n in k.get("levels") or []
            }
        ),
    }


def run_load(
    mode: str = "fixture",
    *,
    data_path: Path | None = None,
    fixture_path: Path | None = None,
    wipe: bool = True,
) -> dict[str, int]:
    """Load SFIA into Neo4j and return live node/relationship counts.

    mode:
        ``fixture`` — committed five-skill subset (tests/CI).
        ``full`` — structure JSON at ``data_path`` or ``SFIA_DATA_PATH``.
    wipe:
        If True (default), detach-delete existing SFIA nodes before load.
    """
    if mode == "fixture":
        doc = load_fixture_document(fixture_path)
    elif mode == "full":
        doc = load_full_document(data_path)
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(f"Normalizing ({mode}) …", flush=True)
    payload = normalize_document(doc)
    _dedupe_payload(payload)
    claimed = _document_expectations(doc)
    for key, count in claimed.items():
        if len(payload[key]) != count:
            raise SfiaLoadValidationError(
                f"SFIA {key}: document has {count} but normalized {len(payload[key])}"
            )
    print(
        f"  unique categories={len(payload['categories']):,} "
        f"subcategories={len(payload['subcategories']):,} "
        f"skills={len(payload['skills']):,} "
        f"skill_levels={len(payload['skill_levels']):,} "
        f"has_level_rows={len(payload['has_level']):,} "
        f"broader_rows={len(payload['broader']):,}",
        flush=True,
    )

    with neo4j_driver() as (driver, database):
        verify_connectivity(driver)
        print("Connected to Neo4j. Loading …", flush=True)
        load_normalized(driver, payload, database=database, wipe=wipe)
        # Expected counts come from the document, not from what MERGE reported.
        expected = {
            **claimed,
            "has_level": len(payload["has_level"]),
            "broader_than": len(payload["broader"]),
        }
        print("Validating …", flush=True)
        live = validate_load(driver, expected, database=database)
    return live


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load SFIA suite into Neo4j")
    parser.add_argument(
        "--mode",
        choices=("fixture", "full"),
        default="fixture",
        help="fixture = committed subset; full = SFIA 9 structure JSON (see fetch.py)",
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=None,
        help="Override SFIA_DATA_PATH for --mode full",
    )
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Do not DETACH DELETE existing SFIA nodes before load",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    # Progress lines use "…" / "→"; a cp1252 Windows console must not abort a
    # half-finished load over them.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")
    try:
        live = run_load(
            mode=args.mode,
            data_path=args.data_path,
            wipe=not args.no_wipe,
        )
    except (ServiceUnavailable, AuthError, ValueError, FileNotFoundError) as exc:
        # Missing config, data or database: one clear line, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "mode": args.mode, "counts": live}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
