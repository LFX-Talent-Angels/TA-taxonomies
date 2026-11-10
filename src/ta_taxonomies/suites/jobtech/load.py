"""JobTech graph loader: build the suite knowledge graph in Neo4j.

Pipeline: read → normalize → MERGE → validate. Querying is tools.py.

Entrypoint::

    python -m ta_taxonomies.suites.jobtech.load --mode fixture
    python -m ta_taxonomies.suites.jobtech.load --mode full   # JOBTECH_DATA_PATH

Both modes read the document ``fetch.py`` writes (the fixture is a subset of
it). The wipe only ever deletes JobTech nodes.
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

from ta_taxonomies.suites.jobtech.config import (
    ESCO_MATCH_KINDS,
    LABEL_JOBTECH_NODE,
    LABEL_OCCUPATION,
    LABEL_SKILL,
    LABEL_SKILL_HEADLINE,
    LABEL_SSYK_GROUP,
    REL_BROADER_THAN,
    REL_CLASSIFIED_UNDER,
    REL_HAS_SKILL,
    REL_SUBSTITUTES,
    RELATION_ESSENTIAL,
    RELATION_GROUP,
    RELATION_OPTIONAL,
    SOURCE,
    TAXONOMY_VERSION,
)
from ta_taxonomies.suites.jobtech.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.jobtech.ids import (
    concept_id,
    headline_id,
    occupation_id,
    skill_id,
    ssyk_code,
    ssyk_id,
)
from ta_taxonomies.suites.jobtech.schema import apply_schema

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fixture.json"
DEFAULT_DATA_PATH = "data/jobtech/jobtech_document.json"
BATCH = 500

NODE_KEYS: tuple[tuple[str, str], ...] = (
    ("ssyk_groups", LABEL_SSYK_GROUP),
    ("headlines", LABEL_SKILL_HEADLINE),
    ("skills", LABEL_SKILL),
    ("occupations", LABEL_OCCUPATION),
)
EDGE_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("group_parents", REL_BROADER_THAN, LABEL_SSYK_GROUP, LABEL_SSYK_GROUP),
    ("skill_headlines", REL_BROADER_THAN, LABEL_SKILL, LABEL_SKILL_HEADLINE),
    ("classified_under", REL_CLASSIFIED_UNDER, LABEL_OCCUPATION, LABEL_SSYK_GROUP),
    ("group_skills", REL_HAS_SKILL, LABEL_SSYK_GROUP, LABEL_SKILL),
    ("occupation_skills", REL_HAS_SKILL, LABEL_OCCUPATION, LABEL_SKILL),
    ("substitutes", REL_SUBSTITUTES, LABEL_OCCUPATION, LABEL_OCCUPATION),
)


class JobtechLoadValidationError(RuntimeError):
    """Raised when normalized JobTech rows cannot be represented faithfully."""


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURE_PATH).read_text(encoding="utf-8"))


def load_full_document(path: Path | None = None) -> dict[str, Any]:
    p = path or Path(os.getenv("JOBTECH_DATA_PATH", DEFAULT_DATA_PATH))
    if not p.exists():
        raise FileNotFoundError(
            f"Missing JobTech document: {p}. Build it with: "
            "python -m ta_taxonomies.suites.jobtech.fetch"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def _node_row(
    *,
    node_id: str,
    source_id: str,
    kind: str,
    pref_label: str,
    code: str,
    description: str = "",
    alt_labels: list[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "source": SOURCE,
        "source_id": source_id,
        "pref_label": pref_label,
        "alt_labels": list(alt_labels or []),
        "description": description,
        "code": code,
        "kind": kind,
        "extra": {k: v for k, v in (extra or {}).items() if v is not None},
    }


def _esco_props(esco: Mapping[str, Any] | None) -> dict[str, list[str]]:
    esco = esco or {}
    return {f"esco_{kind}": [str(u) for u in esco.get(kind) or []] for kind in ESCO_MATCH_KINDS}


def normalize_document(doc: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Translate a JobTech document (fixture or full) into MERGE payloads."""
    version = (doc.get("meta") or {}).get("taxonomy_version")
    if version not in (None, TAXONOMY_VERSION):
        raise JobtechLoadValidationError(
            f"document is JobTech version {version}; this loader is pinned to {TAXONOMY_VERSION}"
        )

    groups: list[dict[str, Any]] = []
    group_parents: list[dict[str, Any]] = []
    group_codes: set[str] = set()
    for g in doc.get("ssyk_groups", []):
        code = ssyk_code(g.get("code"))
        group_codes.add(code)
        groups.append(
            _node_row(
                node_id=ssyk_id(code),
                source_id=concept_id(g.get("concept_id")),
                kind=LABEL_SSYK_GROUP,
                pref_label=str(g.get("label") or ""),
                code=code,
                description=str(g.get("definition") or ""),
                extra={"level": len(code), "ssyk_major": code[0]},
            )
        )
        if g.get("parent"):
            group_parents.append({"from_id": ssyk_id(code), "to_id": ssyk_id(g["parent"])})

    headlines = [
        _node_row(
            node_id=headline_id(h.get("concept_id")),
            source_id=concept_id(h.get("concept_id")),
            kind=LABEL_SKILL_HEADLINE,
            pref_label=str(h.get("label") or ""),
            code=concept_id(h.get("concept_id")),
        )
        for h in doc.get("headlines", [])
    ]
    headline_ids = {row["id"] for row in headlines}

    skills: list[dict[str, Any]] = []
    skill_headlines: list[dict[str, Any]] = []
    group_skills: list[dict[str, Any]] = []
    skills_by_unit: dict[str, list[str]] = {}
    for s in doc.get("skills", []):
        cid = concept_id(s.get("concept_id"))
        sid = skill_id(cid)
        skills.append(
            _node_row(
                node_id=sid,
                source_id=cid,
                kind=LABEL_SKILL,
                pref_label=str(s.get("label") or ""),
                code=cid,
                description=str(s.get("definition") or ""),
                alt_labels=[str(a) for a in s.get("alt_labels") or []],
                extra=_esco_props(s.get("esco")),
            )
        )
        hid = headline_id(s.get("headline"))
        if hid not in headline_ids:
            raise JobtechLoadValidationError(f"skill {cid}: unknown headline {hid}")
        skill_headlines.append({"from_id": sid, "to_id": hid})
        for unit in s.get("ssyk_codes") or []:
            unit = ssyk_code(unit)
            if unit not in group_codes:
                continue  # a fixture may hold only some unit groups
            skills_by_unit.setdefault(unit, []).append(sid)
            group_skills.append(
                {"from_id": ssyk_id(unit), "to_id": sid, "props": {"relation_type": RELATION_GROUP}}
            )
    skill_ids = {row["id"] for row in skills}

    occupations: list[dict[str, Any]] = []
    classified: list[dict[str, Any]] = []
    occupation_skills: list[dict[str, Any]] = []
    for o in doc.get("occupations", []):
        cid = concept_id(o.get("concept_id"))
        oid = occupation_id(cid)
        unit = ssyk_code(o.get("ssyk_code"))
        if unit not in group_codes or len(unit) != 4:
            raise JobtechLoadValidationError(f"occupation {cid}: no SSYK unit group {unit}")
        occupations.append(
            _node_row(
                node_id=oid,
                source_id=cid,
                kind=LABEL_OCCUPATION,
                pref_label=str(o.get("label") or ""),
                code=cid,
                description=str(o.get("definition") or ""),
                alt_labels=[str(a) for a in o.get("alt_labels") or []],
                extra={
                    "ssyk_code": unit,
                    "ssyk_major": unit[0],
                    "isco_codes": [str(c) for c in o.get("isco_codes") or []],
                    **_esco_props(o.get("esco")),
                },
            )
        )
        classified.append({"from_id": oid, "to_id": ssyk_id(unit)})
        direct: dict[str, str] = {}
        for relation in (RELATION_ESSENTIAL, RELATION_OPTIONAL):
            for skill in o.get(relation) or []:
                sid = skill_id(skill)
                if sid in skill_ids:
                    direct.setdefault(sid, relation)
        for sid in skills_by_unit.get(unit, []):
            direct.setdefault(sid, RELATION_GROUP)
        occupation_skills.extend(
            {"from_id": oid, "to_id": sid, "props": {"relation_type": relation}}
            for sid, relation in sorted(direct.items())
        )

    occupation_ids = {row["id"] for row in occupations}
    substitutes = [
        {
            "from_id": occupation_id(s["from"]),
            "to_id": occupation_id(s["to"]),
            "props": {"substitutability_pct": int(s["pct"])},
        }
        for s in doc.get("substitutions", [])
        if occupation_id(s["from"]) in occupation_ids and occupation_id(s["to"]) in occupation_ids
    ]

    return {
        "ssyk_groups": groups,
        "headlines": headlines,
        "skills": skills,
        "occupations": occupations,
        "group_parents": group_parents,
        "skill_headlines": skill_headlines,
        "classified_under": classified,
        "group_skills": group_skills,
        "occupation_skills": occupation_skills,
        "substitutes": substitutes,
    }


def _merge_nodes(session: Session, label: str, rows: list[dict[str, Any]]) -> int:
    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{label} {{id: row.id}})
    SET n:{LABEL_JOBTECH_NODE}
    SET n.source = row.source,
        n.source_id = row.source_id,
        n.pref_label = row.pref_label,
        n.alt_labels = row.alt_labels,
        n.description = row.description,
        n.code = row.code,
        n.kind = row.kind
    SET n += row.extra
    """
    for i in range(0, len(rows), BATCH):
        session.run(cypher, rows=rows[i : i + BATCH])
    return len(rows)


def _merge_edges(
    session: Session, rel: str, from_label: str, to_label: str, rows: list[dict[str, Any]]
) -> int:
    cypher = f"""
    UNWIND $rows AS row
    MATCH (a:{from_label}:{LABEL_JOBTECH_NODE} {{id: row.from_id}})
    MATCH (b:{to_label}:{LABEL_JOBTECH_NODE} {{id: row.to_id}})
    MERGE (a)-[r:{rel}]->(b)
    SET r += coalesce(row.props, {{}})
    RETURN count(*) AS c
    """
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        rec = session.run(cypher, rows=chunk).single()
        matched = int(rec["c"]) if rec else 0
        if matched != len(chunk):
            raise JobtechLoadValidationError(
                f"{rel} merge attempted {len(chunk)} rows but matched {matched}; "
                f"missing endpoints {len(chunk) - matched}"
            )
        total += matched
    return total


def wipe_jobtech_graph(session: Session) -> None:
    """Delete JobTech suite nodes only (never other suites), in batches."""
    session.run(
        f"MATCH (n:{LABEL_JOBTECH_NODE}) WHERE n.source = $source "
        "CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS",
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
            wipe_jobtech_graph(session)
        for key, label in NODE_KEYS:
            print(f"  merging {key.replace('_', ' ')} …", flush=True)
            counts[key] = _merge_nodes(session, label, payload[key])
            print(f"    → {counts[key]:,}", flush=True)
        for key, rel, from_label, to_label in EDGE_KEYS:
            print(f"  merging {key.replace('_', ' ')} ({rel}) …", flush=True)
            counts[key] = _merge_edges(session, rel, from_label, to_label, payload[key])
            print(f"    → {counts[key]:,}", flush=True)
    return counts


def _single_count(session: Session, cypher: str) -> int:
    rec = session.run(cypher, source=SOURCE).single()
    return int(rec["c"]) if rec else 0


def validate_load(
    driver: Driver, expected: Mapping[str, int], *, database: str | None = None
) -> dict[str, int]:
    """Assert load invariants; return live counts."""
    node = LABEL_JOBTECH_NODE
    with driver.session(database=database) as session:
        live = {
            key: _single_count(
                session, f"MATCH (n:{label}:{node}) WHERE n.source = $source RETURN count(n) AS c"
            )
            for key, label in NODE_KEYS
        }
        for key, rel, from_label, to_label in EDGE_KEYS:
            live[key] = _single_count(
                session,
                f"MATCH (a:{from_label}:{node})-[r:{rel}]->(b:{to_label}:{node}) "
                "WHERE a.source = $source AND b.source = $source RETURN count(r) AS c",
            )
        checks = {
            "blank identity nodes": f"""
                MATCH (n:{node}) WHERE n.source = $source
                  AND (n.id IS NULL OR n.id = '' OR n.source_id IS NULL OR n.source_id = ''
                       OR n.pref_label IS NULL OR n.pref_label = '')
                RETURN count(n) AS c""",
            "edges to another suite": f"""
                MATCH (a:{node} {{source: $source}})-[r]-(b)
                WHERE coalesce(b.source, '') <> $source
                RETURN count(r) AS c""",
            "occupations without exactly one SSYK unit group": f"""
                MATCH (o:{LABEL_OCCUPATION}:{node} {{source: $source}})
                WHERE COUNT {{ (o)-[:{REL_CLASSIFIED_UNDER}]->(:{LABEL_SSYK_GROUP} {{level: 4}}) }}
                      <> 1
                RETURN count(o) AS c""",
            "skills without exactly one headline": f"""
                MATCH (s:{LABEL_SKILL}:{node} {{source: $source}})
                WHERE COUNT {{ (s)-[:{REL_BROADER_THAN}]->(:{LABEL_SKILL_HEADLINE}) }} <> 1
                RETURN count(s) AS c""",
            "HAS_SKILL edges with an unknown relation type": f"""
                MATCH (:{node} {{source: $source}})-[r:{REL_HAS_SKILL}]->()
                WHERE NOT r.relation_type IN
                      ['{RELATION_ESSENTIAL}', '{RELATION_OPTIONAL}', '{RELATION_GROUP}']
                RETURN count(r) AS c""",
        }
        failures = {name: _single_count(session, cypher) for name, cypher in checks.items()}

    for name, count in expected.items():
        if live.get(name) != count:
            raise JobtechLoadValidationError(
                f"JobTech {name} count mismatch: expected {count}, found {live.get(name)}"
            )
    for name, count in failures.items():
        if count:
            raise JobtechLoadValidationError(f"{name}: {count}")
    return live


def run_load(
    mode: str = "fixture",
    *,
    data_path: Path | None = None,
    fixture_path: Path | None = None,
    wipe: bool = True,
) -> dict[str, int]:
    """Load JobTech into Neo4j and return live node/relationship counts."""
    if mode == "fixture":
        doc = load_fixture_document(fixture_path)
    elif mode == "full":
        doc = load_full_document(data_path)
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(f"Normalizing ({mode}) …", flush=True)
    payload = normalize_document(doc)
    for key, _label in NODE_KEYS:
        if len({row["id"] for row in payload[key]}) != len(payload[key]):
            raise JobtechLoadValidationError(f"JobTech {key}: duplicate ids in the document")
    expected = {key: len(rows) for key, rows in payload.items()}
    print("  " + " ".join(f"{k}={v:,}" for k, v in expected.items()), flush=True)

    with neo4j_driver() as (driver, database):
        verify_connectivity(driver)
        print("Connected to Neo4j. Loading …", flush=True)
        load_normalized(driver, payload, database=database, wipe=wipe)
        print("Validating …", flush=True)
        live = validate_load(driver, expected, database=database)
    return live


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the JobTech suite into Neo4j")
    parser.add_argument("--mode", choices=("fixture", "full"), default="fixture")
    parser.add_argument("--data-path", type=Path, default=None)
    parser.add_argument("--no-wipe", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")
    try:
        live = run_load(mode=args.mode, data_path=args.data_path, wipe=not args.no_wipe)
    except (ServiceUnavailable, AuthError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "mode": args.mode, "counts": live}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
