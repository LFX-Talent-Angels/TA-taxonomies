"""SFIA graph loader: build the suite knowledge graph in Neo4j.

Use case: reproducible ingestion for CI (small committed fixture) or a local
SFIA snapshot on a developer machine. Pipeline is read → normalize → MERGE →
validate. Querying is tools.py, after the graph exists.

Entrypoint::

    python -m ta_taxonomies.suites.sfia.load --mode fixture
    python -m ta_taxonomies.suites.sfia.load --mode full --data-dir data/sfia/raw

Why it exists, beyond "every suite has one":

* **Pointer, not payload, enforced rather than intended.** ``extract.py`` drops
  SFIA's licensed text at the point of reading; this module re-checks the whole
  payload with ``assert_factual_only`` before the first MERGE, and then checks
  the *graph itself* after the load. A description cannot reach Neo4j by being
  threaded through one more function.
* **The invariants are SFIA's.** Every SFIA skill is defined at at least one
  level, which the loader asserts. No SFIA edge carries an essential/optional
  flag, which the loader also asserts — inverted, as an assertion that the
  flag is *absent*, because inventing one would be the licensed-shaped mistake's
  structural cousin: presenting our modelling as source data.
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

from ta_taxonomies.suites.sfia.config import (
    CANONICAL_LABELS,
    LABEL_CATEGORY,
    LABEL_LEVEL,
    LABEL_SFIA_NODE,
    LABEL_SKILL,
    LABEL_SUBCATEGORY,
    LEVEL_MAX,
    LEVELS,
    LICENSED_FIELD_MARKERS,
    MAX_LABEL_CHARS,
    REL_BROADER_THAN,
    REL_HAS_LEVEL,
    REL_MAY_LEAD_TO,
    REL_RELATED_TO,
    SOURCE,
    VERSION,
)
from ta_taxonomies.suites.sfia.db import neo4j_config_from_env, neo4j_driver, verify_connectivity
from ta_taxonomies.suites.sfia.extract import assert_factual_only, read_source
from ta_taxonomies.suites.sfia.ids import (
    category_id,
    level_id,
    normalize_level,
    normalize_skill_code,
    skill_id,
    skill_slug_to_code,
    subcategory_id,
)
from ta_taxonomies.suites.sfia.schema import apply_schema

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fixture.json"
BATCH = 500

# Where a reader can look up what this suite deliberately does not store. It is
# a URL, not SFIA's text — which is the whole point of "pointer, not payload".
SKILL_PAGE_URL = "https://sfia-online.org/en/sfia-{version}/skills/{slug}"


class SfiaLoadValidationError(RuntimeError):
    """Raised when normalized SFIA rows cannot be represented faithfully."""


# --- normalization ---------------------------------------------------------


def _node_row(
    *,
    node_id: str,
    label_kind: str,
    source_id: str,
    pref_label: str,
    code: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a node row.

    There is deliberately no ``description`` parameter, and no ``alt_labels``
    one either. The first is licensed SFIA text (ADR-0006 §2). The second is a
    field SFIA simply does not publish — one name per skill, no synonyms — and a
    node carrying an always-empty list would invite a Locate tier that can never
    match. The keys this returns are exactly ``ALLOWED_NODE_KEYS``.
    """
    return {
        "id": node_id,
        "source": SOURCE,
        "source_id": source_id,
        "pref_label": pref_label or "",
        "code": code,
        "kind": label_kind,
        "version": VERSION,
        "extra": dict(extra or {}),
    }


def _level_nodes() -> list[dict[str, Any]]:
    """The seven levels of responsibility, materialised on every load.

    They are framework structure rather than rows of data: level 6 exists
    whether or not any loaded skill reaches it, and a fixture that happened to
    stop at level 5 must not produce a graph where level 6 is missing. Deriving
    them from the loaded skills would make the responsibility axis depend on the
    slice, which is the one thing this suite exists to provide.

    The labels are ``"Level 4"``, not SFIA's names for the levels. ADR-0006 §2
    authorises level *numbers*; SFIA's names for its levels are
    SFIA's wording, and where this suite cannot tell factual from descriptive it
    leaves the thing out.
    """
    return [
        _node_row(
            node_id=level_id(level),
            label_kind=LABEL_LEVEL,
            source_id=str(level),
            pref_label=f"Level {level}",
            code=str(level),
            extra={"level": level},
        )
        for level in LEVELS
    ]


def _level_ladder() -> list[dict[str, Any]]:
    """``MAY_LEAD_TO`` from each level to the next one up.

    The ordering is published structure, not our inference: SFIA's levels are
    defined as a progression and the framework's own material describes them as
    a recognisable path from one level to the next. Only adjacent steps are
    materialised — a level-2-to-level-6 edge would assert a jump nobody claims,
    and a path query can still traverse the ladder.
    """
    return [
        {
            "from_id": level_id(level),
            "to_id": level_id(level + 1),
            "from_level": level,
            "to_level": level + 1,
        }
        for level in LEVELS
        if level < LEVEL_MAX
    ]


def normalize_document(doc: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Translate a structure-only SFIA document into MERGE payloads."""
    raw_skills = list(doc.get("skills", []))

    codes_by_slug: dict[str, str] = {
        str(slug): normalize_skill_code(code)
        for slug, code in (doc.get("codes_by_slug") or {}).items()
    }
    for row in raw_skills:
        slug = str(row.get("slug") or "").strip().lower()
        if slug:
            codes_by_slug.setdefault(slug, normalize_skill_code(row["code"]))

    skills: list[dict[str, Any]] = []
    categories: dict[str, dict[str, Any]] = {}
    subcategories: dict[str, dict[str, Any]] = {}
    has_level: list[dict[str, Any]] = []
    broader: list[dict[str, Any]] = []
    related: list[dict[str, Any]] = []
    seen_codes: dict[str, str] = {}

    for row in raw_skills:
        code = normalize_skill_code(row["code"])
        name = str(row.get("name") or "").strip()
        # A code that reappears with a different name means the code is not the
        # identity in this version. Last-wins would hide that behind whichever
        # row happened to be read second, with every count still adding up.
        known = seen_codes.get(code)
        if known is not None and known != name:
            raise SfiaLoadValidationError(
                f"skill code {code} carries two different names ({known!r} then {name!r}); "
                "the code is not a stable identity in this SFIA version"
            )
        seen_codes[code] = name

        levels = sorted({normalize_level(level) for level in row.get("levels") or []})
        slug = str(row.get("slug") or "").strip().lower()
        extra: dict[str, Any] = {
            "levels": levels,
            "level_count": len(levels),
            "min_level": levels[0] if levels else None,
            "max_level": levels[-1] if levels else None,
        }
        if slug:
            # A pointer to where the definition lives, for a caller who holds
            # their own SFIA access. Storing the address is what lets this suite
            # be useful without storing the text.
            extra["page_slug"] = slug
            extra["source_url"] = SKILL_PAGE_URL.format(version=VERSION, slug=slug)

        category = str(row.get("category") or "").strip()
        subcategory = str(row.get("subcategory") or "").strip()
        if category:
            cat_id = category_id(category)
            categories.setdefault(
                cat_id,
                _node_row(
                    node_id=cat_id,
                    label_kind=LABEL_CATEGORY,
                    source_id=category,
                    pref_label=category,
                ),
            )
            parent_id = cat_id
            if subcategory:
                sub_id = subcategory_id(category, subcategory)
                subcategories.setdefault(
                    sub_id,
                    _node_row(
                        node_id=sub_id,
                        label_kind=LABEL_SUBCATEGORY,
                        source_id=f"{category}/{subcategory}",
                        pref_label=subcategory,
                        extra={"category": category},
                    ),
                )
                broader.append({"from_id": sub_id, "to_id": cat_id})
                parent_id = sub_id
            broader.append({"from_id": skill_id(code), "to_id": parent_id})

        skills.append(
            _node_row(
                node_id=skill_id(code),
                label_kind=LABEL_SKILL,
                source_id=code,
                pref_label=name,
                code=code,
                extra=extra,
            )
        )
        for level in levels:
            has_level.append(
                {
                    "from_id": skill_id(code),
                    "to_id": level_id(level),
                    "level": level,
                }
            )

    known_codes = {row["code"] for row in skills}
    for row in raw_skills:
        code = normalize_skill_code(row["code"])
        for slug in row.get("related_slugs") or []:
            other = skill_slug_to_code(slug, codes_by_slug)
            # A related-skill link whose target is outside the loaded slice is a
            # missing endpoint, not a node to invent. Fixtures hold a slice, so
            # this is the normal case there rather than an error.
            if other is None or other not in known_codes or other == code:
                continue
            related.append({"from_id": skill_id(code), "to_id": skill_id(other)})

    return {
        "levels": _level_nodes(),
        "categories": list(categories.values()),
        "subcategories": list(subcategories.values()),
        "skills": skills,
        "has_level": has_level,
        "broader_than": broader,
        "related_to": related,
        "may_lead_to": _level_ladder(),
    }


# --- merge -----------------------------------------------------------------


def _merge_nodes(session: Session, label: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    canonical = CANONICAL_LABELS[label]
    # MERGE on the umbrella label, then apply the kind and canonical labels.
    #
    # MERGE matches on the *whole* pattern, labels included, so merging on a
    # kind label means a node already holding this id under any other label set
    # is invisible and gets duplicated instead of matched — a load that succeeds,
    # validates clean, and leaves two nodes sharing one id. The umbrella is the
    # one label every node of this suite carries and the one its uniqueness
    # constraint is on, which makes it both the correct identity and an indexed
    # lookup. It is still not proof against a node that lacks the umbrella
    # entirely — nothing indexed can be — so validate_load checks for that.
    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{LABEL_SFIA_NODE} {{id: row.id}})
    SET n:{label}, n:{canonical}
    SET n.source = row.source,
        n.source_id = row.source_id,
        n.pref_label = row.pref_label,
        n.code = row.code,
        n.kind = row.kind,
        n.version = row.version
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


# Properties are set from an explicit map rather than `r += row` so the join
# keys never leak onto the relationship and the edge's shape is readable here.
#
# Note what is *not* set: `relation_type`. TA-agents filters neighbours on it
# and defaults the value to "essential", so these edges will be filtered out by
# an ESCO-shaped caller. That is the correct outcome — SFIA publishes no
# essential/optional distinction and no occupation-to-skill edge to carry one —
# and `get_neighbors` says so in its warnings rather than fabricating a value.
_HAS_LEVEL_CYPHER = f"""
UNWIND $rows AS row
MATCH (s:{LABEL_SKILL} {{id: row.from_id}})
MATCH (l:{LABEL_LEVEL} {{id: row.to_id}})
MERGE (s)-[r:{REL_HAS_LEVEL}]->(l)
SET r.level = row.level
RETURN count(*) AS c
"""

# Endpoints may be a skill, a subcategory or a category, so match the umbrella.
_BROADER_THAN_CYPHER = f"""
UNWIND $rows AS row
MATCH (a:{LABEL_SFIA_NODE} {{id: row.from_id}})
MATCH (b:{LABEL_SFIA_NODE} {{id: row.to_id}})
MERGE (a)-[:{REL_BROADER_THAN}]->(b)
RETURN count(*) AS c
"""

_RELATED_TO_CYPHER = f"""
UNWIND $rows AS row
MATCH (a:{LABEL_SKILL} {{id: row.from_id}})
MATCH (b:{LABEL_SKILL} {{id: row.to_id}})
MERGE (a)-[:{REL_RELATED_TO}]->(b)
RETURN count(*) AS c
"""

_MAY_LEAD_TO_CYPHER = f"""
UNWIND $rows AS row
MATCH (a:{LABEL_LEVEL} {{id: row.from_id}})
MATCH (b:{LABEL_LEVEL} {{id: row.to_id}})
MERGE (a)-[r:{REL_MAY_LEAD_TO}]->(b)
SET r.from_level = row.from_level,
    r.to_level = row.to_level
RETURN count(*) AS c
"""


def wipe_sfia_graph(session: Session) -> None:
    """Delete this suite's nodes only.

    Scoped by ``source`` **and** by the suite umbrella label, because the
    canonical labels (``:Skill``, ``:Level``) are shared with every other suite
    in the same graph.
    """
    session.run(
        f"""
        MATCH (n:{LABEL_SFIA_NODE})
        WHERE n.source = $source
        DETACH DELETE n
        """,
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
    with driver.session(database=database) as session:
        if wipe:
            wipe_sfia_graph(session)

        counts: dict[str, int] = {}
        for key, label in (
            ("levels", LABEL_LEVEL),
            ("categories", LABEL_CATEGORY),
            ("subcategories", LABEL_SUBCATEGORY),
            ("skills", LABEL_SKILL),
        ):
            print(f"  merging {key} …", flush=True)
            counts[key] = _merge_nodes(session, label, payload[key])
            print(f"    → {counts[key]:,}", flush=True)

        for key, cypher, rel in (
            ("has_level", _HAS_LEVEL_CYPHER, REL_HAS_LEVEL),
            ("broader_than", _BROADER_THAN_CYPHER, REL_BROADER_THAN),
            ("related_to", _RELATED_TO_CYPHER, REL_RELATED_TO),
            ("may_lead_to", _MAY_LEAD_TO_CYPHER, REL_MAY_LEAD_TO),
        ):
            print(f"  merging {rel} …", flush=True)
            counts[key] = _merge_rel_count(session, cypher, payload[key], relationship=rel)
            print(f"    → {counts[key]:,}", flush=True)

    return counts


def validate_load(
    driver: Driver,
    expected: Mapping[str, int],
    *,
    database: str | None = None,
) -> dict[str, int]:
    """Assert load invariants; return live counts.

    The invariants are SFIA's, and two of them are unusual enough to name:

    * **Every skill is defined at at least one level.** Unlike O*NET, where 122
      occupations carry no ratings and the equivalent assertion would refuse
      correct data, a SFIA skill that reaches no level does not exist in the
      framework — so a skill without ``HAS_LEVEL`` means extraction dropped
      something, and the load should stop rather than publish a skill with no
      position on the axis this suite exists to provide.
    * **No edge carries ``relation_type``.** This is an assertion that a value
      is *absent*, which is unusual, and it is here because the tempting
      shortcut is to invent one so ESCO-shaped callers keep working. Doing so
      would present our modelling as SFIA's, which is the same failure as
      storing SFIA's prose: both put something in the graph that the source
      never said.

    The licence guard runs here too, against the graph rather than the payload:
    a stored string longer than a label, or a property whose name stems from
    licensed prose, fails the load.
    """
    with driver.session(database=database) as session:

        def count_label(label: str) -> int:
            rec = session.run(
                f"MATCH (n:{label}) WHERE n.source = $source RETURN count(n) AS c",
                source=SOURCE,
            ).single()
            return int(rec["c"]) if rec else 0

        def count_rel(rel: str) -> int:
            rec = session.run(
                f"MATCH (a:{LABEL_SFIA_NODE})-[r:{rel}]->(b:{LABEL_SFIA_NODE}) "
                "WHERE a.source = $source AND b.source = $source RETURN count(r) AS c",
                source=SOURCE,
            ).single()
            return int(rec["c"]) if rec else 0

        live = {
            "levels": count_label(LABEL_LEVEL),
            "categories": count_label(LABEL_CATEGORY),
            "subcategories": count_label(LABEL_SUBCATEGORY),
            "skills": count_label(LABEL_SKILL),
            "has_level": count_rel(REL_HAS_LEVEL),
            "broader_than": count_rel(REL_BROADER_THAN),
            "related_to": count_rel(REL_RELATED_TO),
            "may_lead_to": count_rel(REL_MAY_LEAD_TO),
        }

        def scalar(cypher: str, **params: Any) -> int:
            rec = session.run(cypher, source=SOURCE, **params).single()
            return int(rec["c"]) if rec else 0

        dangling = scalar(
            f"""
            MATCH (s:{LABEL_SKILL} {{source: $source}})-[r:{REL_HAS_LEVEL}]->(x)
            WHERE NOT x:{LABEL_LEVEL}
            RETURN count(r) AS c
            """
        )
        blank = scalar(
            f"""
            MATCH (n:{LABEL_SFIA_NODE})
            WHERE n.id IS NULL OR n.id = '' OR n.source IS NULL
              OR n.source_id IS NULL OR n.source_id = ''
            RETURN count(n) AS c
            """
        )
        # A node holding one of this suite's ids without the suite's umbrella
        # label. The uniqueness constraint cannot see it — constraints are per
        # label — so MERGE creates a second node and every count still adds up.
        # A crosswalk that materialises an endpoint before its suite is loaded
        # leaves exactly such a node, and the wipe does not remove it because
        # deleting another package's node is not this loader's call.
        impostors = scalar(
            f"""
            MATCH (n)
            WHERE n.id STARTS WITH '{SOURCE}:' AND NOT n:{LABEL_SFIA_NODE}
            RETURN count(n) AS c
            """
        )
        levelless = scalar(
            f"""
            MATCH (s:{LABEL_SKILL} {{source: $source}})
            WHERE NOT (s)-[:{REL_HAS_LEVEL}]->(:{LABEL_LEVEL})
            RETURN count(s) AS c
            """
        )
        invented_binary = scalar(
            f"""
            MATCH (:{LABEL_SFIA_NODE} {{source: $source}})
                  -[r]->(:{LABEL_SFIA_NODE} {{source: $source}})
            WHERE r.relation_type IS NOT NULL
            RETURN count(r) AS c
            """
        )
        # Licence guard, post-load: the payload check in extract.py cannot see a
        # property written by anything other than this loader, and the graph is
        # what gets published.
        long_strings = scalar(
            f"""
            MATCH (n:{LABEL_SFIA_NODE} {{source: $source}})
            UNWIND keys(n) AS k
            WITH n, k WHERE n[k] IS :: STRING AND size(n[k]) > $ceiling
            RETURN count(*) AS c
            """,
            ceiling=MAX_LABEL_CHARS,
        )
        licensed_keys = scalar(
            f"""
            MATCH (n:{LABEL_SFIA_NODE} {{source: $source}})
            UNWIND keys(n) AS k
            WITH k WHERE any(marker IN $markers WHERE toLower(k) CONTAINS marker)
            RETURN count(*) AS c
            """,
            markers=list(LICENSED_FIELD_MARKERS),
        )

    for name, expected_count in expected.items():
        actual = live[name]
        if actual != expected_count:
            raise SfiaLoadValidationError(
                f"SFIA {name} count mismatch: expected {expected_count}, found {actual}"
            )
    if live["levels"] != len(LEVELS):
        raise SfiaLoadValidationError(
            f"SFIA defines {len(LEVELS)} levels of responsibility; found {live['levels']}"
        )
    if live["may_lead_to"] != len(LEVELS) - 1:
        raise SfiaLoadValidationError(
            f"the level ladder needs {len(LEVELS) - 1} steps; found {live['may_lead_to']}"
        )
    if dangling:
        raise SfiaLoadValidationError(f"dangling HAS_LEVEL edges: {dangling}")
    if blank:
        raise SfiaLoadValidationError(f"blank identity nodes: {blank}")
    if impostors:
        raise SfiaLoadValidationError(
            f"{impostors} node(s) carry an '{SOURCE}:' id without the "
            f":{LABEL_SFIA_NODE} label, so they duplicate this suite's identities; "
            "label them or remove them before loading"
        )
    if levelless:
        raise SfiaLoadValidationError(
            f"{levelless} skill(s) reach no level of responsibility; "
            "every SFIA skill is defined at at least one level, so extraction dropped something"
        )
    if invented_binary:
        raise SfiaLoadValidationError(
            f"{invented_binary} edge(s) carry relation_type. SFIA publishes no "
            "essential/optional distinction; a suite that invents one presents its own "
            "modelling as source data (ADR-0006 §3)"
        )
    if long_strings:
        raise SfiaLoadValidationError(
            f"{long_strings} stored string(s) exceed the {MAX_LABEL_CHARS}-character label "
            "ceiling; SFIA's descriptive text may not be stored in this repository"
        )
    if licensed_keys:
        raise SfiaLoadValidationError(
            f"{licensed_keys} node propert(ies) are named after licensed SFIA prose; "
            "descriptions are fetched at runtime by users holding their own SFIA access"
        )
    return live


# --- documents -------------------------------------------------------------


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURE_PATH).read_text(encoding="utf-8"))


def _dedupe_edges(rows: list[dict[str, Any]], *, label: str = "") -> list[dict[str, Any]]:
    """Keep one row per (from_id, to_id); last row wins for properties.

    Deduplication is legitimate — MERGE would collapse the pair anyway, and the
    expected counts have to match what MERGE produces. What is not legitimate is
    doing it silently: a source that starts publishing two rows per pair would
    lose one set of properties with every count still adding up. So the count is
    printed.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        seen[(row["from_id"], row["to_id"])] = row
    dropped = len(rows) - len(seen)
    if dropped and label:
        print(f"  note: {label} collapsed {dropped:,} duplicate endpoint pairs", flush=True)
    return list(seen.values())


def run_load(
    mode: str = "fixture",
    *,
    data_dir: Path | None = None,
    fixture_path: Path | None = None,
    wipe: bool = True,
) -> dict[str, int]:
    """Load SFIA into Neo4j and return live node/relationship counts.

    mode:
        ``fixture`` — committed structure-only subset (tests/CI).
        ``full`` — local snapshot or CSV export under ``data_dir`` or
        ``SFIA_DATA_DIR``. Never committed: see fetch.py and ADR-0006 §2.
    wipe:
        If True (default), detach-delete existing SFIA nodes before load.
    """
    if mode == "fixture":
        doc = load_fixture_document(fixture_path)
    elif mode == "full":
        root = data_dir or Path(os.getenv("SFIA_DATA_DIR", "data/sfia/raw"))
        doc = read_source(root)
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(f"Normalizing ({mode}) …", flush=True)
    payload = normalize_document(doc)

    for key in ("levels", "categories", "subcategories", "skills"):
        unique: dict[str, dict[str, Any]] = {}
        for row in payload[key]:
            unique[row["id"]] = row
        payload[key] = list(unique.values())
        # The licence guard, before anything is written. extract.py already
        # dropped SFIA's text at the point of reading; this re-checks the
        # finished rows, so a property added anywhere in normalize is caught too.
        assert_factual_only(payload[key], what=f"sfia {key}")
    for key in ("has_level", "broader_than", "related_to", "may_lead_to"):
        payload[key] = _dedupe_edges(payload[key], label=key)

    meta = doc.get("meta") or {}
    print(
        "  unique " + " ".join(f"{k}={len(payload[k]):,}" for k in sorted(payload)),
        flush=True,
    )
    if not payload["categories"]:
        # Said out loud rather than left to be inferred from a zero: SFIA's
        # category view is behind a login, so a snapshot-built load legitimately
        # has no category tree and validation must not demand one.
        print(
            "  note: no category tree in this source "
            f"(has_category_tree={meta.get('has_category_tree')})",
            flush=True,
        )

    cfg = neo4j_config_from_env()
    # Printed before anything is deleted: a load starts by wiping this suite's
    # nodes, and the URI is the one thing worth being sure about first.
    print(f"Target: {cfg['uri']} database={cfg['database']} (wipe={wipe})", flush=True)

    with neo4j_driver() as (driver, database):
        verify_connectivity(driver)
        counts = load_normalized(driver, payload, database=database, wipe=wipe)
        expected = {
            "levels": counts["levels"],
            "categories": counts["categories"],
            "subcategories": counts["subcategories"],
            "skills": counts["skills"],
            "has_level": len(payload["has_level"]),
            "broader_than": len(payload["broader_than"]),
            "related_to": len(payload["related_to"]),
            "may_lead_to": len(payload["may_lead_to"]),
        }
        print("Validating …", flush=True)
        live = validate_load(driver, expected, database=database)
    return live


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the SFIA suite into Neo4j")
    parser.add_argument(
        "--mode",
        choices=("fixture", "full"),
        required=True,
        help=(
            "fixture = committed structure-only subset; full = local SFIA snapshot. "
            "Required on purpose: a load wipes this suite's nodes, so the target "
            "must be a choice rather than a default."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override SFIA_DATA_DIR for --mode full",
    )
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Do not DETACH DELETE existing SFIA nodes before load",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    live = run_load(mode=args.mode, data_dir=args.data_dir, wipe=not args.no_wipe)
    print(json.dumps({"ok": True, "mode": args.mode, "version": VERSION, "counts": live}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
