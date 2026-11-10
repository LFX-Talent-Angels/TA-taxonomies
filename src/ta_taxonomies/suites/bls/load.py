"""BLS graph loader: build the suite knowledge graph in Neo4j.

Use case: reproducible ingestion for CI (small committed fixture) or the full
BLS document on a developer machine / Aura. Pipeline is
read → normalize → MERGE → validate. Querying is tools.py.

Entrypoint::

    python -m ta_taxonomies.suites.bls.load --mode fixture
    python -m ta_taxonomies.suites.bls.load --mode full   # BLS_DATA_PATH

Both modes read the same document shape: ``fetch.py`` writes it from the BLS
spreadsheets, and the fixture is a subset of it, so one
``normalize_document`` serves both. The wipe only ever deletes BLS nodes.
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

from ta_taxonomies.suites.bls.config import (
    EDUCATION_LEVELS,
    ESSENTIAL_MIN_SCORE,
    LABEL_BLS_NODE,
    LABEL_EDUCATION,
    LABEL_OCCUPATION,
    LABEL_SKILL,
    LABEL_SOC_GROUP,
    LABEL_TRAINING,
    NO_TRAINING,
    OPTIONAL_MIN_SCORE,
    PROJECTION_CYCLE,
    REL_BROADER_THAN,
    REL_CLASSIFIED_UNDER,
    REL_HAS_SKILL,
    REL_REQUIRES_EDUCATION,
    REL_REQUIRES_TRAINING,
    SCORE_RATINGS,
    SOURCE,
    TRAINING_LEVELS,
    WAGE_PERIOD,
)
from ta_taxonomies.suites.bls.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.bls.ids import (
    education_id,
    group_id,
    occupation_id,
    skill_id,
    soc_code,
    soc_level,
    training_id,
)
from ta_taxonomies.suites.bls.schema import apply_schema

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fixture.json"
DEFAULT_DATA_PATH = "data/bls/bls_document.json"
BATCH = 200

# Node buckets in load order (parents first, so edges always find endpoints).
NODE_KEYS: tuple[tuple[str, str], ...] = (
    ("groups", LABEL_SOC_GROUP),
    ("skills", LABEL_SKILL),
    ("education_levels", LABEL_EDUCATION),
    ("training_levels", LABEL_TRAINING),
    ("occupations", LABEL_OCCUPATION),
)
# Edge buckets: (payload key, relationship, from label, to label).
EDGE_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("broader", REL_BROADER_THAN, LABEL_SOC_GROUP, LABEL_SOC_GROUP),
    ("classified_under", REL_CLASSIFIED_UNDER, LABEL_OCCUPATION, LABEL_SOC_GROUP),
    ("has_skill", REL_HAS_SKILL, LABEL_OCCUPATION, LABEL_SKILL),
    ("requires_education", REL_REQUIRES_EDUCATION, LABEL_OCCUPATION, LABEL_EDUCATION),
    ("requires_training", REL_REQUIRES_TRAINING, LABEL_OCCUPATION, LABEL_TRAINING),
)
#: Occupation properties copied from the EP projection, as stored names.
PROJECTION_FIELDS: tuple[str, ...] = (
    "employment_2025",
    "employment_2035",
    "employment_change",
    "employment_change_pct",
    "openings_annual",
    "median_wage_annual",
    "entry_education",
    "work_experience",
    "job_training",
    "ooh_url",
)


class BlsLoadValidationError(RuntimeError):
    """Raised when normalized BLS rows cannot be represented faithfully."""


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURE_PATH).read_text(encoding="utf-8"))


def load_full_document(path: Path | None = None) -> dict[str, Any]:
    """Read the full BLS document (``BLS_DATA_PATH``, written by fetch.py)."""
    p = path or Path(os.getenv("BLS_DATA_PATH", DEFAULT_DATA_PATH))
    if not p.exists():
        raise FileNotFoundError(
            f"Missing BLS document: {p}. Build it with: "
            "python -m ta_taxonomies.suites.bls.fetch (needs BLS_USER_AGENT)"
        )
    return json.loads(p.read_text(encoding="utf-8"))


# -- normalize -----------------------------------------------------------------


def rating_for(score: float) -> str:
    """BLS's words for an EP skill score ("Very important" for 3.5 to 4.5)."""
    for lower, rating in SCORE_RATINGS:
        if score >= lower:
            return rating
    return SCORE_RATINGS[-1][1]


def relation_type_for(score: float) -> str | None:
    """Talent Angels policy: essential ≥ 3.5, optional ≥ 2.5, else no edge."""
    if score >= ESSENTIAL_MIN_SCORE:
        return "essential"
    if score >= OPTIONAL_MIN_SCORE:
        return "optional"
    return None


_ES_PLURAL = ("ches", "shes", "sses", "xes")


def singular_title(title: str) -> str | None:
    """The singular of a plain plural SOC title ("Registered Nurses" -> "Registered Nurse").

    BLS titles are plural and users type the singular. Titles that list
    several jobs ("Chefs and Head Cooks", "Lawyers, Judges, ...") are left
    alone: no single singular names them.
    """
    title = title.removesuffix(", General")
    if "," in title or "--" in title or " and " in title or "/" in title:
        return None
    # "Supervisors of Gambling Services Workers": the noun before "of" is plural.
    noun_phrase, of, rest = title.partition(" of ")
    head, _, last = noun_phrase.rpartition(" ")
    word = _singular_word(last)
    if word is None:
        return None
    return f"{head} {word}".strip() + (f"{of}{rest}" if of else "")


def _singular_word(word: str) -> str | None:
    lower = word.lower()
    if lower.endswith("ies") and len(lower) > 4:
        return word[:-3] + "y"
    if lower.endswith("wives"):
        return word[:-3] + "fe"
    if lower.endswith(_ES_PLURAL):
        return word[:-2]
    if lower.endswith("s") and not lower.endswith(("ss", "us", "is")):
        return word[:-1]
    return None


def _node_row(
    *,
    node_id: str,
    source_id: str,
    kind: str,
    pref_label: str,
    code: str | None = None,
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
        # Neo4j cannot store a null in a map it SETs; drop missing facts.
        "extra": {k: v for k, v in (extra or {}).items() if v is not None},
    }


def _category_row(
    name: str, levels: tuple[str, ...], kind: str, make_id: Any, what: str
) -> dict[str, Any]:
    if name not in levels:
        raise BlsLoadValidationError(
            f"unknown {what} {name!r}; BLS changed its categories (update config.py)"
        )
    return _node_row(
        node_id=make_id(name),
        source_id=name,
        kind=kind,
        pref_label=name,
        extra={"rank": levels.index(name) + 1},
    )


def normalize_document(doc: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Translate a BLS document (fixture or full) into MERGE payloads."""
    groups: list[dict[str, Any]] = []
    broader: list[dict[str, Any]] = []
    group_ids: set[str] = set()
    for index, g in enumerate(doc.get("groups", [])):
        code = soc_code(g.get("code"))
        level = soc_level(code)
        if level == "detailed":
            raise BlsLoadValidationError(f"groups row {index}: {code} is an occupation")
        gid = group_id(code)
        group_ids.add(gid)
        groups.append(
            _node_row(
                node_id=gid,
                source_id=code,
                kind=LABEL_SOC_GROUP,
                pref_label=str(g.get("title") or ""),
                code=code,
                extra={"level": level},
            )
        )
        # broad -> minor -> major, as the SOC structure file orders them.
        parent = g.get("parent")
        if level != "major":
            broader.append({"from_id": gid, "to_id": group_id(soc_code(parent))})

    skills: list[dict[str, Any]] = []
    skill_names: set[str] = set()
    for s in doc.get("skills", []):
        name = str(s.get("name") or "")
        skill_names.add(name)
        skills.append(
            _node_row(
                node_id=skill_id(name),
                source_id=str(s.get("ep_id") or name),
                kind=LABEL_SKILL,
                pref_label=name,
                description=str(s.get("definition") or ""),
                extra={"onet_elements": list(s.get("onet_elements") or [])},
            )
        )

    occupations: list[dict[str, Any]] = []
    classified: list[dict[str, Any]] = []
    has_skill: list[dict[str, Any]] = []
    requires_education: list[dict[str, Any]] = []
    requires_training: list[dict[str, Any]] = []
    education: dict[str, dict[str, Any]] = {}
    training: dict[str, dict[str, Any]] = {}
    for o in doc.get("occupations", []):
        code = soc_code(o.get("code"))
        if soc_level(code) != "detailed":
            raise BlsLoadValidationError(f"occupation {code} is a group code")
        oid = occupation_id(code)
        projection = o.get("projection") or {}
        extra: dict[str, Any] = {
            "soc_code": code,
            "soc_major": code[:2],
            "onet_soc_codes": list(o.get("onet_soc_codes") or []),
        }
        if projection:
            extra.update({field: projection.get(field) for field in PROJECTION_FIELDS})
            extra.update(
                {
                    "projection_code": projection.get("code"),
                    "projection_aggregate": bool(projection.get("aggregate")),
                    "projection_cycle": PROJECTION_CYCLE,
                    "wage_period": WAGE_PERIOD,
                }
            )
        title = str(o.get("title") or "")
        alt_labels = [str(t) for t in o.get("alt_titles") or []]
        singular = singular_title(title)
        if singular and singular.casefold() not in {a.casefold() for a in alt_labels}:
            alt_labels.append(singular)
        occupations.append(
            _node_row(
                node_id=oid,
                source_id=code,
                kind=LABEL_OCCUPATION,
                pref_label=title,
                code=code,
                description=str(o.get("definition") or ""),
                alt_labels=alt_labels,
                extra=extra,
            )
        )
        broad_id = group_id(soc_code(o.get("group")))
        if broad_id not in group_ids:
            raise BlsLoadValidationError(f"occupation {code}: no broad group {broad_id}")
        classified.append({"from_id": oid, "to_id": broad_id})

        for skill in o.get("skills") or []:
            name, score = str(skill.get("name")), float(skill.get("score"))
            if name not in skill_names:
                raise BlsLoadValidationError(f"occupation {code}: unknown skill {name!r}")
            relation = relation_type_for(score)
            if relation is None:
                continue
            has_skill.append(
                {
                    "from_id": oid,
                    "to_id": skill_id(name),
                    "props": {
                        "score": score,
                        "rating": rating_for(score),
                        "relation_type": relation,
                    },
                }
            )

        entry = projection.get("entry_education")
        if entry:
            row = _category_row(
                entry, EDUCATION_LEVELS, LABEL_EDUCATION, education_id, "entry education"
            )
            education[row["id"]] = row
            requires_education.append(
                {"from_id": oid, "to_id": row["id"], "props": {"typical_entry": True}}
            )
        train = projection.get("job_training")
        if train and train != NO_TRAINING:
            row = _category_row(train, TRAINING_LEVELS, LABEL_TRAINING, training_id, "training")
            training[row["id"]] = row
            requires_training.append({"from_id": oid, "to_id": row["id"], "props": {}})

    return {
        "groups": groups,
        "skills": skills,
        "education_levels": sorted(education.values(), key=lambda r: r["extra"]["rank"]),
        "training_levels": sorted(training.values(), key=lambda r: r["extra"]["rank"]),
        "occupations": occupations,
        "broader": broader,
        "classified_under": classified,
        "has_skill": has_skill,
        "requires_education": requires_education,
        "requires_training": requires_training,
    }


# -- MERGE ---------------------------------------------------------------------


def _merge_nodes(session: Session, label: str, rows: list[dict[str, Any]]) -> int:
    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{label} {{id: row.id}})
    SET n:{LABEL_BLS_NODE}
    SET n.source = row.source,
        n.source_id = row.source_id,
        n.pref_label = row.pref_label,
        n.alt_labels = row.alt_labels,
        n.description = row.description,
        n.code = row.code,
        n.kind = row.kind
    SET n += row.extra
    """
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        session.run(cypher, rows=chunk)
        total += len(chunk)
    return total


def _merge_edges(
    session: Session,
    rel: str,
    from_label: str,
    to_label: str,
    rows: list[dict[str, Any]],
) -> int:
    """Batched MERGE, failing clearly when any endpoint is missing."""
    cypher = f"""
    UNWIND $rows AS row
    MATCH (a:{from_label}:{LABEL_BLS_NODE} {{id: row.from_id}})
    MATCH (b:{to_label}:{LABEL_BLS_NODE} {{id: row.to_id}})
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
            raise BlsLoadValidationError(
                f"{rel} merge attempted {len(chunk)} rows but matched {matched}; "
                f"missing endpoints {len(chunk) - matched}"
            )
        total += matched
    return total


def wipe_bls_graph(session: Session) -> None:
    """Delete BLS suite nodes only (never the whole database / other suites)."""
    session.run(
        f"MATCH (n:{LABEL_BLS_NODE}) WHERE n.source = $source DETACH DELETE n",
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
            wipe_bls_graph(session)
        for key, label in NODE_KEYS:
            print(f"  merging {key.replace('_', ' ')} …", flush=True)
            counts[key] = _merge_nodes(session, label, payload[key])
            print(f"    → {counts[key]:,}", flush=True)
        for key, rel, from_label, to_label in EDGE_KEYS:
            print(f"  merging {rel} …", flush=True)
            counts[key] = _merge_edges(session, rel, from_label, to_label, payload[key])
            print(f"    → {counts[key]:,}", flush=True)
    return counts


# -- validate ------------------------------------------------------------------


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
    node = LABEL_BLS_NODE
    with driver.session(database=database) as session:

        def count_label(label: str) -> int:
            return _single_count(
                session,
                f"MATCH (n:{label}:{node}) WHERE n.source = $source RETURN count(n) AS c",
            )

        def count_rel(rel: str) -> int:
            return _single_count(
                session,
                f"MATCH (a:{node})-[r:{rel}]->(b:{node}) "
                "WHERE a.source = $source AND b.source = $source RETURN count(r) AS c",
            )

        live = {key: count_label(label) for key, label in NODE_KEYS}
        live.update({key: count_rel(rel) for key, rel, _f, _t in EDGE_KEYS})

        checks = {
            "blank identity nodes": f"""
                MATCH (n:{node}) WHERE n.source = $source
                  AND (n.id IS NULL OR n.id = '' OR n.source_id IS NULL OR n.source_id = ''
                       OR n.pref_label IS NULL OR n.pref_label = '')
                RETURN count(n) AS c
            """,
            "edges to another suite": f"""
                MATCH (a:{node} {{source: $source}})-[r]-(b)
                WHERE coalesce(b.source, '') <> $source
                RETURN count(r) AS c
            """,
            "occupations without exactly one broad group": f"""
                MATCH (o:{LABEL_OCCUPATION}:{node} {{source: $source}})
                WHERE COUNT {{ (o)-[:{REL_CLASSIFIED_UNDER}]->(:{LABEL_SOC_GROUP}) }} <> 1
                RETURN count(o) AS c
            """,
            "broad or minor groups without exactly one parent": f"""
                MATCH (g:{LABEL_SOC_GROUP}:{node} {{source: $source}})
                WHERE g.level IN ['broad', 'minor']
                  AND COUNT {{ (g)-[:{REL_BROADER_THAN}]->(:{LABEL_SOC_GROUP}) }} <> 1
                RETURN count(g) AS c
            """,
            "HAS_SKILL edges outside the policy": f"""
                MATCH (:{node} {{source: $source}})-[r:{REL_HAS_SKILL}]->()
                WHERE NOT (
                    (r.relation_type = 'essential' AND r.score >= {ESSENTIAL_MIN_SCORE})
                    OR (r.relation_type = 'optional' AND r.score >= {OPTIONAL_MIN_SCORE}
                        AND r.score < {ESSENTIAL_MIN_SCORE}))
                RETURN count(r) AS c
            """,
        }
        failures = {name: _single_count(session, cypher) for name, cypher in checks.items()}

    for name, expected_count in expected.items():
        if live.get(name) != expected_count:
            raise BlsLoadValidationError(
                f"BLS {name} count mismatch: expected {expected_count}, found {live.get(name)}"
            )
    for name, count in failures.items():
        if count:
            raise BlsLoadValidationError(f"{name}: {count}")
    return live


def run_load(
    mode: str = "fixture",
    *,
    data_path: Path | None = None,
    fixture_path: Path | None = None,
    wipe: bool = True,
) -> dict[str, int]:
    """Load BLS into Neo4j and return live node/relationship counts.

    mode:
        ``fixture`` — committed subset (tests/CI).
        ``full`` — the document at ``data_path`` or ``BLS_DATA_PATH``.
    wipe:
        If True (default), detach-delete existing BLS nodes before load.
    """
    if mode == "fixture":
        doc = load_fixture_document(fixture_path)
    elif mode == "full":
        doc = load_full_document(data_path)
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(f"Normalizing ({mode}) …", flush=True)
    payload = normalize_document(doc)
    expected = {key: len(rows) for key, rows in payload.items()}
    for key, _label in NODE_KEYS:
        if len({row["id"] for row in payload[key]}) != len(payload[key]):
            raise BlsLoadValidationError(f"BLS {key}: duplicate ids in the document")
    print("  " + " ".join(f"{k}={v:,}" for k, v in expected.items()), flush=True)

    with neo4j_driver() as (driver, database):
        verify_connectivity(driver)
        print("Connected to Neo4j. Loading …", flush=True)
        load_normalized(driver, payload, database=database, wipe=wipe)
        print("Validating …", flush=True)
        live = validate_load(driver, expected, database=database)
    return live


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the BLS suite into Neo4j")
    parser.add_argument(
        "--mode",
        choices=("fixture", "full"),
        default="fixture",
        help="fixture = committed subset; full = the BLS document (see fetch.py)",
    )
    parser.add_argument(
        "--data-path", type=Path, default=None, help="Override BLS_DATA_PATH for --mode full"
    )
    parser.add_argument(
        "--no-wipe", action="store_true", help="Do not DETACH DELETE existing BLS nodes first"
    )
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
