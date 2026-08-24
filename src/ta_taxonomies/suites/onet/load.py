"""O*NET graph loader: build the suite knowledge graph in Neo4j.

Use case: reproducible ingestion for CI (small committed fixture) or the full
O*NET 30.3 text distribution on a developer machine. Pipeline is
read → normalize → MERGE → validate. Querying is tools.py, after the graph
exists.

Entrypoint::

    python -m ta_taxonomies.suites.onet.load --mode fixture
    python -m ta_taxonomies.suites.onet.load --mode full

Why it exists: one path for fixture and full data (same normalize + merge),
pointer-not-payload (the distribution stays gitignored), and post-load
assertions that are true of O*NET rather than inherited from ESCO — notably
that 122 of the 1,016 occupations carry **no** descriptor ratings at all, so
"every occupation has at least one skill edge" is a bug here, not a check.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from neo4j import Driver, Session

from ta_taxonomies.suites.onet.config import (
    CANONICAL_LABELS,
    ESSENTIAL_IMPORTANCE_MIN,
    IM_MAX,
    IM_MIN,
    LABEL_ELEMENT,
    LABEL_ELEMENT_GROUP,
    LABEL_OCCUPATION,
    LABEL_ONET_NODE,
    LABEL_SOC_GROUP,
    LABEL_TASK,
    RATED_FILES,
    REL_BROADER_THAN,
    REL_CLASSIFIED_UNDER,
    REL_HAS_SKILL,
    REL_PERFORMS_TASK,
    REL_RELATED_TO,
    RELATION_TYPE_POLICY,
    RELEASE,
    SCALE_IMPORTANCE,
    SCALE_LEVEL,
    SOURCE,
)
from ta_taxonomies.suites.onet.db import neo4j_config_from_env, neo4j_driver, verify_connectivity
from ta_taxonomies.suites.onet.ids import (
    code_to_str,
    dedupe_titles,
    element_ancestors,
    element_id,
    element_parent_id,
    normalize_element_id,
    normalize_onetsoc_code,
    occupation_id,
    soc_code_from_onetsoc,
    soc_group_id,
    task_id,
)
from ta_taxonomies.suites.onet.schema import apply_schema

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fixture.json"
BATCH = 500

# O*NET's null in text columns. Not a value, not an empty string.
NA = "n/a"

# Which edge property each rating scale writes, for conflict detection.
_VALUE_FIELD = {SCALE_IMPORTANCE: "importance", SCALE_LEVEL: "level"}


class OnetLoadValidationError(RuntimeError):
    """Raised when normalized O*NET rows cannot be represented faithfully."""


# --- source reading --------------------------------------------------------


def read_tsv(path: Path) -> list[dict[str, str]]:
    """Read one tab-delimited O*NET table.

    ``quoting=csv.QUOTE_NONE``: task statements and element descriptions
    contain apostrophes and inch marks, and the default dialect would treat a
    stray double quote as the start of a quoted field and swallow the rest of
    the row.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))


def _text(row: Mapping[str, Any], key: str) -> str:
    value = str(row.get(key) or "").strip()
    return "" if value.lower() == NA else value


def _float(row: Mapping[str, Any], key: str) -> float | None:
    value = _text(row, key)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int(row: Mapping[str, Any], key: str) -> int | None:
    value = _text(row, key)
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _yn(row: Mapping[str, Any], key: str) -> bool | None:
    """O*NET's Y / N / n/a flags. ``None`` means the flag does not apply."""
    value = _text(row, key).upper()
    if value == "Y":
        return True
    if value == "N":
        return False
    return None


# --- normalization ---------------------------------------------------------


def _node_row(
    *,
    node_id: str,
    label_kind: str,
    source_id: str,
    pref_label: str,
    alt_labels: Iterable[str] = (),
    description: str = "",
    code: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "source": SOURCE,
        "source_id": source_id,
        "pref_label": pref_label or "",
        "alt_labels": list(alt_labels),
        "description": description or "",
        "code": code,
        "kind": label_kind,
        "release": RELEASE,
        "extra": dict(extra or {}),
    }


def _relation_type(importance: float | None) -> str | None:
    """Project Importance onto ESCO's essential/optional binary.

    Declared policy, not source data — see ``RELATION_TYPE_POLICY`` in
    config.py for why a suite with real numbers ships a coarser flag anyway.
    Returns None when there is no Importance rating to project.
    """
    if importance is None:
        return None
    return "essential" if importance >= ESSENTIAL_IMPORTANCE_MIN else "optional"


def _normalize_rated_rows(
    rows: Iterable[Mapping[str, Any]],
    domain: str,
    prefix: str,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, str]]:
    """Pair the IM and LV rows of each (occupation, element) onto one edge.

    O*NET publishes two rows per rating — Importance and Level — each with its
    own sample size, standard error and confidence bounds. They describe the
    same relationship, so they belong on one edge with both sets of statistics
    preserved rather than on two edges that would double every count.
    """
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    element_names: dict[str, str] = {}

    for row in rows:
        code = normalize_onetsoc_code(row["O*NET-SOC Code"])
        element = normalize_element_id(row["Element ID"])
        if not element.startswith(prefix):
            raise OnetLoadValidationError(
                f"element {element!r} in the {domain} table does not start with {prefix!r}; "
                "the O*NET taxonomy was renumbered and this loader's assumptions are stale"
            )
        name = _text(row, "Element Name")
        if name:
            element_names.setdefault(element, name)

        key = (code, element)
        edge = edges.get(key)
        if edge is None:
            edge = {
                "from_id": occupation_id(code),
                "to_id": element_id(element),
                "content_model_domain": domain,
                "importance": None,
                "level": None,
                "importance_n": None,
                "level_n": None,
                "importance_standard_error": None,
                "importance_lower_ci": None,
                "importance_upper_ci": None,
                "level_lower_ci": None,
                "level_upper_ci": None,
                "recommend_suppress": False,
                "not_relevant": False,
                "date": "",
                "domain_source": "",
            }
            edges[key] = edge

        scale = _text(row, "Scale ID")
        value = _float(row, "Data Value")
        # A repeated (occupation, element, scale) would overwrite the earlier
        # value and every count would still add up, so the loss would be
        # indistinguishable from the rating never having been published.
        # Release 30.3 has none; this exists so that a release that does have
        # them stops the load instead of quietly picking whichever row came
        # last. An identical repeat is harmless and allowed.
        previous = edge.get(_VALUE_FIELD.get(scale, ""))
        if previous is not None and value is not None and previous != value:
            raise OnetLoadValidationError(
                f"conflicting {scale} ratings for {code} / {element}: "
                f"{previous} then {value}; the source table has duplicate rows "
                "and this loader would silently keep the last one"
            )
        if scale == SCALE_IMPORTANCE:
            edge["importance"] = value
            edge["importance_n"] = _int(row, "N")
            edge["importance_standard_error"] = _float(row, "Standard Error")
            edge["importance_lower_ci"] = _float(row, "Lower CI Bound")
            edge["importance_upper_ci"] = _float(row, "Upper CI Bound")
        elif scale == SCALE_LEVEL:
            edge["level"] = value
            edge["level_n"] = _int(row, "N")
            edge["level_lower_ci"] = _float(row, "Lower CI Bound")
            edge["level_upper_ci"] = _float(row, "Upper CI Bound")
        else:
            # Other scales exist in O*NET but not in these four tables.
            continue

        # Either row may carry the flag; a rating O*NET disowns on one scale is
        # not trustworthy on the other.
        if _yn(row, "Recommend Suppress"):
            edge["recommend_suppress"] = True
        if _yn(row, "Not Relevant"):
            edge["not_relevant"] = True
        edge["date"] = _text(row, "Date") or edge["date"]
        edge["domain_source"] = _text(row, "Domain Source") or edge["domain_source"]

    for edge in edges.values():
        importance = edge["importance"]
        if importance is not None and not (IM_MIN <= importance <= IM_MAX):
            raise OnetLoadValidationError(
                f"importance {importance} outside the published {IM_MIN}–{IM_MAX} scale"
            )
        edge["relation_type"] = _relation_type(importance)
        edge["relation_type_policy"] = RELATION_TYPE_POLICY.name
        edge["relation_type_policy_version"] = RELATION_TYPE_POLICY.version

    return edges, element_names


def normalize_document(doc: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Translate an O*NET document (fixture JSON or full-table dict) into MERGE payloads."""
    occupations: list[dict[str, Any]] = []
    soc_groups: dict[str, dict[str, Any]] = {}
    classified: list[dict[str, Any]] = []

    titles_by_code: dict[str, list[str]] = {}
    for row in list(doc.get("job_titles", [])) + list(doc.get("reported_titles", [])):
        code = normalize_onetsoc_code(row["O*NET-SOC Code"])
        title = _text(row, "Job Title") or _text(row, "Reported Job Title")
        if title:
            titles_by_code.setdefault(code, []).append(title)

    for row in doc.get("occupations", []):
        code = normalize_onetsoc_code(row["O*NET-SOC Code"])
        title = _text(row, "Title")
        soc = soc_code_from_onetsoc(code)
        # The occupation's own title is not an alias of itself.
        aliases = [t for t in dedupe_titles(titles_by_code.get(code, [])) if t != title]
        occupations.append(
            _node_row(
                node_id=occupation_id(code),
                label_kind=LABEL_OCCUPATION,
                source_id=code,
                pref_label=title,
                alt_labels=aliases,
                description=_text(row, "Description"),
                code=code,
                extra={
                    "soc_code": soc,
                    "onetsoc_extension": code.split(".", 1)[1],
                    "alt_label_count": len(aliases),
                },
            )
        )
        if soc not in soc_groups:
            soc_groups[soc] = _node_row(
                node_id=soc_group_id(soc),
                label_kind=LABEL_SOC_GROUP,
                source_id=soc,
                # SOC group titles are not in the O*NET text distribution; the
                # ".00" occupation is coextensive with its SOC code, so its
                # title is the SOC's, and a split SOC gets no borrowed label.
                pref_label=title if code.endswith(".00") else "",
                code=soc,
                extra={"taxonomy": "2018 SOC", "derived_from": "O*NET-SOC code prefix"},
            )
        elif code.endswith(".00") and not soc_groups[soc]["pref_label"]:
            soc_groups[soc]["pref_label"] = title
        classified.append({"from_id": occupation_id(code), "to_id": soc_group_id(soc)})

    # Descriptor elements, one edge per (occupation, element) with IM+LV paired.
    has_skill: list[dict[str, Any]] = []
    element_names: dict[str, str] = {}
    element_domains: dict[str, str] = {}
    for filename, (domain, prefix) in RATED_FILES.items():
        rows = doc.get(filename)
        if not rows:
            continue
        edges, names = _normalize_rated_rows(rows, domain, prefix)
        has_skill.extend(edges.values())
        element_names.update(names)
        for element in names:
            element_domains[element] = domain

    content_model = {
        normalize_element_id(row["Element ID"]): row for row in doc.get("content_model", [])
    }

    # Element nodes: the rated leaves, plus every ancestor they hang from. The
    # Content Model tree is encoded in the Element ID, so parentage needs no
    # join table — only the reference file, for names and descriptions.
    leaf_ids = set(element_names)
    group_ids: set[str] = set()
    for leaf in leaf_ids:
        group_ids.update(element_ancestors(leaf))
    group_ids -= leaf_ids

    def _element_node(element: str, label_kind: str) -> dict[str, Any]:
        ref = content_model.get(element, {})
        name = _text(ref, "Element Name") or element_names.get(element, "")
        extra: dict[str, Any] = {"element_id": element}
        if element in element_domains:
            extra["content_model_domain"] = element_domains[element]
        return _node_row(
            node_id=element_id(element),
            label_kind=label_kind,
            source_id=element,
            pref_label=name,
            description=_text(ref, "Description"),
            code=element,
            extra=extra,
        )

    elements = [_element_node(e, LABEL_ELEMENT) for e in sorted(leaf_ids)]
    element_groups = [_element_node(e, LABEL_ELEMENT_GROUP) for e in sorted(group_ids)]

    broader: list[dict[str, Any]] = []
    for element in sorted(leaf_ids | group_ids):
        parent = element_parent_id(element)
        if parent is not None and parent in (leaf_ids | group_ids):
            broader.append({"from_id": element_id(element), "to_id": element_id(parent)})

    tasks: list[dict[str, Any]] = []
    performs: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    seen_task_text: dict[str, str] = {}
    for row in doc.get("tasks", []):
        code = normalize_onetsoc_code(row["O*NET-SOC Code"])
        tid = code_to_str(row["Task ID"]) or ""
        statement = _text(row, "Task")
        # Task IDs are global in O*NET, so the same id reappearing with
        # different text would mean the identifier is not the identity. First
        # wins would hide that behind a task node whose text belongs to
        # whichever occupation the reader happened to load first.
        known = seen_task_text.get(tid)
        if known is not None and known != statement:
            raise OnetLoadValidationError(
                f"Task ID {tid} carries two different statements; "
                "the id is not a stable identity in this release"
            )
        seen_task_text[tid] = statement
        if tid not in seen_tasks:
            seen_tasks.add(tid)
            tasks.append(
                _node_row(
                    node_id=task_id(tid),
                    label_kind=LABEL_TASK,
                    source_id=tid,
                    # A task statement is a sentence, not a name; it is still
                    # the only label there is, and Locate matches against it.
                    pref_label=statement,
                    code=tid,
                    extra={"task_type": _text(row, "Task Type")},
                )
            )
        performs.append(
            {
                "from_id": occupation_id(code),
                "to_id": task_id(tid),
                "task_type": _text(row, "Task Type"),
                "incumbents_responding": _int(row, "Incumbents Responding"),
            }
        )

    related: list[dict[str, Any]] = []
    known_codes = {row["code"] for row in occupations}
    for row in doc.get("related_occupations", []):
        code = normalize_onetsoc_code(row["O*NET-SOC Code"])
        other = normalize_onetsoc_code(row["Related O*NET-SOC Code"])
        # Fixtures hold a slice, so a related occupation may not be present.
        # Dropping the edge is correct; a dangling one would fail validation.
        if code not in known_codes or other not in known_codes:
            continue
        related.append(
            {
                "from_id": occupation_id(code),
                "to_id": occupation_id(other),
                "relatedness_tier": _text(row, "Relatedness Tier"),
                "index": _int(row, "Index"),
            }
        )

    return {
        "occupations": occupations,
        "soc_groups": list(soc_groups.values()),
        "elements": elements,
        "element_groups": element_groups,
        "tasks": tasks,
        "has_skill": has_skill,
        "performs_task": performs,
        "classified_under": classified,
        "related_to": related,
        "broader_than": broader,
    }


# --- merge -----------------------------------------------------------------


def _merge_nodes(session: Session, label: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    canonical = CANONICAL_LABELS[label]
    # MERGE on the umbrella label, then apply the kind and canonical labels.
    #
    # MERGE matches on the *whole* pattern, labels included, so merging on a
    # kind label means a node holding this id under any other label set is
    # invisible and gets duplicated instead of matched. The umbrella is the one
    # label every node of this suite carries and the one its uniqueness
    # constraint is on, which makes it both the correct identity and an indexed
    # lookup. It is still not proof against a node that lacks it entirely —
    # nothing indexed can be — so validate_load checks for that separately.
    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{LABEL_ONET_NODE} {{id: row.id}})
    SET n:{label}, n:{canonical}
    SET n.source = row.source,
        n.source_id = row.source_id,
        n.pref_label = row.pref_label,
        n.alt_labels = row.alt_labels,
        n.description = row.description,
        n.code = row.code,
        n.kind = row.kind,
        n.release = row.release
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
            raise OnetLoadValidationError(
                f"{relationship} merge attempted {len(chunk)} rows but matched {matched}; "
                f"missing endpoints {len(chunk) - matched}"
            )
        total += matched
    return total


# Properties are set from an explicit map rather than `r += row` so that the
# join keys (from_id / to_id) never leak onto the relationship, and so the
# edge's shape is readable here instead of inferred from the payload.
_HAS_SKILL_CYPHER = f"""
UNWIND $rows AS row
MATCH (o:{LABEL_OCCUPATION} {{id: row.from_id}})
MATCH (e:{LABEL_ELEMENT} {{id: row.to_id}})
MERGE (o)-[r:{REL_HAS_SKILL}]->(e)
SET r.importance = row.importance,
    r.level = row.level,
    r.importance_n = row.importance_n,
    r.level_n = row.level_n,
    r.importance_standard_error = row.importance_standard_error,
    r.importance_lower_ci = row.importance_lower_ci,
    r.importance_upper_ci = row.importance_upper_ci,
    r.level_lower_ci = row.level_lower_ci,
    r.level_upper_ci = row.level_upper_ci,
    r.recommend_suppress = row.recommend_suppress,
    r.not_relevant = row.not_relevant,
    r.content_model_domain = row.content_model_domain,
    r.date = row.date,
    r.domain_source = row.domain_source,
    r.relation_type = row.relation_type,
    r.relation_type_policy = row.relation_type_policy,
    r.relation_type_policy_version = row.relation_type_policy_version
RETURN count(*) AS c
"""

_PERFORMS_TASK_CYPHER = f"""
UNWIND $rows AS row
MATCH (o:{LABEL_OCCUPATION} {{id: row.from_id}})
MATCH (t:{LABEL_TASK} {{id: row.to_id}})
MERGE (o)-[r:{REL_PERFORMS_TASK}]->(t)
SET r.task_type = row.task_type,
    r.incumbents_responding = row.incumbents_responding
RETURN count(*) AS c
"""

_CLASSIFIED_UNDER_CYPHER = f"""
UNWIND $rows AS row
MATCH (o:{LABEL_OCCUPATION} {{id: row.from_id}})
MATCH (g:{LABEL_SOC_GROUP} {{id: row.to_id}})
MERGE (o)-[:{REL_CLASSIFIED_UNDER}]->(g)
RETURN count(*) AS c
"""

_RELATED_TO_CYPHER = f"""
UNWIND $rows AS row
MATCH (a:{LABEL_OCCUPATION} {{id: row.from_id}})
MATCH (b:{LABEL_OCCUPATION} {{id: row.to_id}})
MERGE (a)-[r:{REL_RELATED_TO}]->(b)
SET r.relatedness_tier = row.relatedness_tier,
    r.index = row.index
RETURN count(*) AS c
"""

# Endpoints may be an element or an element group, so match the umbrella label.
_BROADER_THAN_CYPHER = f"""
UNWIND $rows AS row
MATCH (a:{LABEL_ONET_NODE} {{id: row.from_id}})
MATCH (b:{LABEL_ONET_NODE} {{id: row.to_id}})
MERGE (a)-[:{REL_BROADER_THAN}]->(b)
RETURN count(*) AS c
"""


def wipe_onet_graph(session: Session) -> None:
    """Delete this suite's nodes only.

    Scoped by ``source`` **and** by the suite-scoped labels, because the
    canonical labels (``:Occupation``, ``:Skill``) are shared with every other
    suite in the same graph.
    """
    session.run(
        f"""
        MATCH (n:{LABEL_ONET_NODE})
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
            wipe_onet_graph(session)

        counts: dict[str, int] = {}
        for key, label in (
            ("soc_groups", LABEL_SOC_GROUP),
            ("occupations", LABEL_OCCUPATION),
            ("element_groups", LABEL_ELEMENT_GROUP),
            ("elements", LABEL_ELEMENT),
            ("tasks", LABEL_TASK),
        ):
            print(f"  merging {key} …", flush=True)
            counts[key] = _merge_nodes(session, label, payload[key])
            print(f"    → {counts[key]:,}", flush=True)

        for key, cypher, rel in (
            ("classified_under", _CLASSIFIED_UNDER_CYPHER, REL_CLASSIFIED_UNDER),
            ("broader_than", _BROADER_THAN_CYPHER, REL_BROADER_THAN),
            ("has_skill", _HAS_SKILL_CYPHER, REL_HAS_SKILL),
            ("performs_task", _PERFORMS_TASK_CYPHER, REL_PERFORMS_TASK),
            ("related_to", _RELATED_TO_CYPHER, REL_RELATED_TO),
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

    The invariants are O*NET's, not ESCO's. In particular there is **no**
    "every occupation has a skill edge" assertion: 122 of the 1,016
    occupations in release 30.3 carry no descriptor ratings at all (they are
    mostly "All Other" residual categories), and a suite that demanded
    otherwise would refuse to load correct data.
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
                f"MATCH (a:{LABEL_ONET_NODE})-[r:{rel}]->(b:{LABEL_ONET_NODE}) "
                "WHERE a.source = $source AND b.source = $source RETURN count(r) AS c",
                source=SOURCE,
            ).single()
            return int(rec["c"]) if rec else 0

        live = {
            "occupations": count_label(LABEL_OCCUPATION),
            "soc_groups": count_label(LABEL_SOC_GROUP),
            "elements": count_label(LABEL_ELEMENT),
            "element_groups": count_label(LABEL_ELEMENT_GROUP),
            "tasks": count_label(LABEL_TASK),
            "has_skill": count_rel(REL_HAS_SKILL),
            "performs_task": count_rel(REL_PERFORMS_TASK),
            "classified_under": count_rel(REL_CLASSIFIED_UNDER),
            "related_to": count_rel(REL_RELATED_TO),
            "broader_than": count_rel(REL_BROADER_THAN),
        }

        def scalar(cypher: str) -> int:
            rec = session.run(cypher, source=SOURCE).single()
            return int(rec["c"]) if rec else 0

        dangling = scalar(
            f"""
            MATCH (o:{LABEL_OCCUPATION} {{source: $source}})-[r:{REL_HAS_SKILL}]->(x)
            WHERE NOT x:{LABEL_ELEMENT}
            RETURN count(r) AS c
            """
        )
        blank = scalar(
            f"""
            MATCH (n:{LABEL_ONET_NODE})
            WHERE n.id IS NULL OR n.id = '' OR n.source IS NULL
              OR n.source_id IS NULL OR n.source_id = ''
            RETURN count(n) AS c
            """
        )
        # A node holding one of this suite's ids without the suite's umbrella
        # label. The uniqueness constraint cannot see it — constraints are per
        # label — so MERGE creates a second node and every count still adds up.
        # Left undetected, two nodes share one id and the identity rule that
        # the whole graph rests on is quietly false.
        #
        # This is not hypothetical: a crosswalk that materialises an endpoint
        # before its suite is loaded leaves exactly such a node, and the wipe
        # does not remove it because deleting another package's node is not
        # this loader's call. Failing here names it instead.
        impostors = scalar(
            f"""
            MATCH (n)
            WHERE n.id STARTS WITH '{SOURCE}:' AND NOT n:{LABEL_ONET_NODE}
            RETURN count(n) AS c
            """
        )
        unclassified = scalar(
            f"""
            MATCH (o:{LABEL_OCCUPATION} {{source: $source}})
            WHERE NOT (o)-[:{REL_CLASSIFIED_UNDER}]->(:{LABEL_SOC_GROUP})
            RETURN count(o) AS c
            """
        )
        # Every rated edge must carry the Importance it was scored from; a
        # HAS_SKILL edge with no importance means the IM row went missing.
        unrated = scalar(
            f"""
            MATCH (:{LABEL_OCCUPATION} {{source: $source}})
                  -[r:{REL_HAS_SKILL}]->(:{LABEL_ELEMENT})
            WHERE r.importance IS NULL
            RETURN count(r) AS c
            """
        )
        # The binary flag is a declared projection; an edge carrying the value
        # but not the policy that produced it would read as source data.
        unlabelled_policy = scalar(
            f"""
            MATCH (:{LABEL_OCCUPATION} {{source: $source}})
                  -[r:{REL_HAS_SKILL}]->(:{LABEL_ELEMENT})
            WHERE r.relation_type IS NOT NULL AND r.relation_type_policy IS NULL
            RETURN count(r) AS c
            """
        )

    for name, expected_count in expected.items():
        actual = live[name]
        if actual != expected_count:
            raise OnetLoadValidationError(
                f"O*NET {name} count mismatch: expected {expected_count}, found {actual}"
            )
    if dangling:
        raise OnetLoadValidationError(f"dangling HAS_SKILL edges: {dangling}")
    if blank:
        raise OnetLoadValidationError(f"blank identity nodes: {blank}")
    if impostors:
        raise OnetLoadValidationError(
            f"{impostors} node(s) carry an '{SOURCE}:' id without the "
            f":{LABEL_ONET_NODE} label, so they duplicate this suite's identities; "
            "label them or remove them before loading"
        )
    if unclassified:
        raise OnetLoadValidationError(f"occupations with no SOC group: {unclassified}")
    if unrated:
        raise OnetLoadValidationError(f"HAS_SKILL edges without importance: {unrated}")
    if unlabelled_policy:
        raise OnetLoadValidationError(
            f"HAS_SKILL edges carrying relation_type without its policy: {unlabelled_policy}"
        )
    return live


# --- documents -------------------------------------------------------------


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURE_PATH).read_text(encoding="utf-8"))


def load_full_document(data_dir: Path) -> dict[str, Any]:
    """Build a fixture-shaped document from the O*NET text distribution.

    ``data_dir`` is the extracted ``db_30_3_text`` folder. Only the tables this
    suite models are read; the distribution ships 45.
    """
    required = {
        "occupations": "Occupation Data.txt",
        "content_model": "Content Model Reference.txt",
        "tasks": "Task Statements.txt",
    }
    optional = {
        "job_titles": "Job Titles.txt",
        "reported_titles": "Sample of Reported Titles.txt",
        "related_occupations": "Related Occupations.txt",
    }
    doc: dict[str, Any] = {
        "meta": {
            "suite": SOURCE,
            "mode": "full",
            "release": RELEASE,
            "data_dir": str(data_dir),
        }
    }
    for key, filename in required.items():
        path = data_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing O*NET table: {path}")
        print(f"  reading {filename} …", flush=True)
        doc[key] = read_tsv(path)
        print(f"    → {len(doc[key]):,} rows", flush=True)

    for filename in RATED_FILES:
        path = data_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing O*NET table: {path}")
        print(f"  reading {filename} …", flush=True)
        doc[filename] = read_tsv(path)
        print(f"    → {len(doc[filename]):,} rows", flush=True)

    for key, filename in optional.items():
        path = data_dir / filename
        if path.exists():
            print(f"  reading {filename} (optional) …", flush=True)
            doc[key] = read_tsv(path)
            print(f"    → {len(doc[key]):,} rows", flush=True)
        else:
            doc[key] = []
    return doc


def _dedupe_edges(rows: list[dict[str, Any]], *, label: str = "") -> list[dict[str, Any]]:
    """Keep one row per (from_id, to_id); last row wins for properties.

    Deduplication is legitimate — MERGE would collapse the pair anyway, and the
    expected counts have to match what MERGE produces. What is not legitimate
    is doing it silently: a source that starts publishing two rows per pair
    would lose one set of properties with every count still adding up. So the
    count is printed. Release 30.3 drops nothing.
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
    """Load O*NET into Neo4j and return live node/relationship counts.

    mode:
        ``fixture`` — committed ICT subset (tests/CI).
        ``full`` — extracted text distribution under ``data_dir`` or
        ``ONET_DATA_DIR``.
    wipe:
        If True (default), detach-delete existing O*NET nodes before load.
    """
    if mode == "fixture":
        doc = load_fixture_document(fixture_path)
    elif mode == "full":
        root = data_dir or Path(os.getenv("ONET_DATA_DIR", "data/onet/raw/db_30_3_text"))
        doc = load_full_document(root)
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(f"Normalizing ({mode}) …", flush=True)
    payload = normalize_document(doc)

    for key in ("occupations", "soc_groups", "elements", "element_groups", "tasks"):
        unique: dict[str, dict[str, Any]] = {}
        for row in payload[key]:
            unique[row["id"]] = row
        payload[key] = list(unique.values())
    for key in ("has_skill", "performs_task", "classified_under", "related_to", "broader_than"):
        payload[key] = _dedupe_edges(payload[key], label=key)

    suppressed = sum(1 for r in payload["has_skill"] if r["recommend_suppress"])
    not_relevant = sum(1 for r in payload["has_skill"] if r["not_relevant"])
    print(
        "  unique "
        + " ".join(f"{k}={len(payload[k]):,}" for k in sorted(payload))
        + f"\n  HAS_SKILL flagged: recommend_suppress={suppressed:,} not_relevant={not_relevant:,}",
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
            "occupations": counts["occupations"],
            "soc_groups": counts["soc_groups"],
            "elements": counts["elements"],
            "element_groups": counts["element_groups"],
            "tasks": counts["tasks"],
            "has_skill": len(payload["has_skill"]),
            "performs_task": len(payload["performs_task"]),
            "classified_under": len(payload["classified_under"]),
            "related_to": len(payload["related_to"]),
            "broader_than": len(payload["broader_than"]),
        }
        print("Validating …", flush=True)
        live = validate_load(driver, expected, database=database)
    return live


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the O*NET suite into Neo4j")
    parser.add_argument(
        "--mode",
        choices=("fixture", "full"),
        required=True,
        help=(
            "fixture = committed subset; full = extracted O*NET text distribution. "
            "Required on purpose: a load wipes this suite's nodes, so the target "
            "must be a choice rather than a default."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override ONET_DATA_DIR for --mode full",
    )
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Do not DETACH DELETE existing O*NET nodes before load",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    live = run_load(mode=args.mode, data_dir=args.data_dir, wipe=not args.no_wipe)
    print(json.dumps({"ok": True, "mode": args.mode, "release": RELEASE, "counts": live}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
