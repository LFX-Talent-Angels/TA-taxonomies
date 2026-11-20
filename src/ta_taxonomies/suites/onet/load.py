"""O*NET graph loader: build the suite knowledge graph in Neo4j.

Use case: reproducible ingestion for CI (small committed fixture) or the
full 31.0 text dump. Pipeline is read → normalize → MERGE → validate.
Does not implement Locate/Connect; that is tools.py after the graph exists.

Entrypoint::

    python -m ta_taxonomies.suites.onet.load --mode fixture
    python -m ta_taxonomies.suites.onet.load --mode full

Why it exists: one path for fixture and full data (same normalize + merge),
pointer-not-payload (dumps stay gitignored), suite-scoped wipe so ESCO in
the same Neo4j survives. Target DB is whatever NEO4J_* env points at.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from neo4j import Driver, Session

from ta_taxonomies.suites.onet.config import (
    LABEL_ABILITY,
    LABEL_DETAILED_WORK_ACTIVITY,
    LABEL_INTEREST,
    LABEL_INTERMEDIATE_WORK_ACTIVITY,
    LABEL_JOB_ZONE,
    LABEL_KNOWLEDGE,
    LABEL_OCCUPATION,
    LABEL_ONET_NODE,
    LABEL_SCALE,
    LABEL_SKILL,
    LABEL_SOFTWARE,
    LABEL_TASK,
    LABEL_WORK_ACTIVITY,
    LABEL_WORK_CONTEXT,
    LABEL_WORK_STYLE,
    REL_BROADER_THAN,
    REL_HAS_ABILITY,
    REL_HAS_EDUCATION,
    REL_HAS_INTEREST,
    REL_HAS_JOB_ZONE,
    REL_HAS_KNOWLEDGE,
    REL_HAS_SKILL,
    REL_HAS_TASK_RATING,
    REL_HAS_TRAINING,
    REL_HAS_WORK_ACTIVITY,
    REL_HAS_WORK_CONTEXT,
    REL_HAS_WORK_STYLE,
    REL_PERFORMS_TASK,
    REL_RELATED_TO,
    REL_USES_SOFTWARE,
    SOURCE,
)
from ta_taxonomies.suites.onet.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.onet.ids import (
    OnetIdError,
    software_slug,
    suite_id_element,
    suite_id_job_zone,
    suite_id_occupation,
    suite_id_scale,
    suite_id_software,
    suite_id_task,
)
from ta_taxonomies.suites.onet.schema import apply_schema

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fixture.json"
BATCH = 200
DOWNLOAD_URL = "https://www.onetcenter.org/dl_files/database/db_31_0_text.zip"

REQUIRED_FILES: dict[str, str] = {
    "occupation_data": "Occupation Data.txt",
    "job_titles": "Job Titles.txt",
    "sample_of_reported_titles": "Sample of Reported Titles.txt",
    "task_statements": "Task Statements.txt",
    "task_ratings": "Task Ratings.txt",
    "task_categories": "Task Categories.txt",
    "emerging_tasks": "Emerging Tasks.txt",
    "essential_skills": "Essential Skills.txt",
    "transferable_skills": "Transferable Skills.txt",
    "knowledge": "Knowledge.txt",
    "abilities": "Abilities.txt",
    "work_activities": "Work Activities.txt",
    "work_context": "Work Context.txt",
    "work_styles": "Work Styles.txt",
    "software_skills": "Software Skills.txt",
    "related_occupations": "Related Occupations.txt",
    "job_zones": "Job Zones.txt",
    "job_zone_reference": "Job Zone Reference.txt",
    "education": "Education.txt",
    "education_categories": "Education Categories.txt",
    "training_and_experience": "Training and Experience.txt",
    "training_and_experience_categories": "Training and Experience Categories.txt",
    "career_interest_types": "Career Interest Types.txt",
    "specific_interest_areas": "Specific Interest Areas.txt",
    "specific_interest_areas_to_career_interest_types": (
        "Specific Interest Areas to Career Interest Types.txt"
    ),
    "career_interest_type_keywords": "Career Interest Type Keywords.txt",
    "interests_illustrative_activities": "Interests Illustrative Activities.txt",
    "interests_illustrative_occupations": "Interests Illustrative Occupations.txt",
    "content_model_reference": "Content Model Reference.txt",
    "gwas_to_iwas": "GWAs to IWAs.txt",
    "gwas_to_iwas_to_dwas": "GWAs to IWAs to DWAs.txt",
    "tasks_to_dwas": "Tasks to DWAs.txt",
    "abilities_to_work_activities": "Abilities to Work Activities.txt",
    "abilities_to_work_context": "Abilities to Work Context.txt",
    "essential_skills_to_work_activities": "Essential Skills to Work Activities.txt",
    "essential_skills_to_work_context": "Essential Skills to Work Context.txt",
    "transferable_skills_to_work_activities": "Transferable Skills to Work Activities.txt",
    "transferable_skills_to_work_context": "Transferable Skills to Work Context.txt",
    "work_styles_to_work_activities": "Work Styles to Work Activities.txt",
    "work_styles_to_work_context": "Work Styles to Work Context.txt",
    "scales_reference": "Scales Reference.txt",
    "level_scale_anchors": "Level Scale Anchors.txt",
    "work_context_categories": "Work Context Categories.txt",
}

_KIND_PREFIXES: tuple[tuple[str, str], ...] = (
    ("4.A", LABEL_WORK_ACTIVITY),
    ("4.C", LABEL_WORK_CONTEXT),
    ("2.A", LABEL_SKILL),
    ("2.B", LABEL_SKILL),
    ("2.C", LABEL_KNOWLEDGE),
    ("2.D", LABEL_SKILL),
    ("2.E", LABEL_SKILL),
    ("1.A", LABEL_ABILITY),
    ("1.B", LABEL_INTEREST),
    ("1.D", LABEL_WORK_STYLE),
    ("1.C", LABEL_INTEREST),
)


class OnetLoadValidationError(RuntimeError):
    """Raised when normalized O*NET rows cannot be represented faithfully."""


def _fixture_path() -> Path:
    return FIXTURE_PATH


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    p = path or _fixture_path()
    return json.loads(p.read_text(encoding="utf-8"))


def _cell(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _num(value: Any) -> float | None:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"n/a", "na"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def kind_for_element_id(element_id: str) -> str:
    eid = element_id.strip()
    for prefix, label in _KIND_PREFIXES:
        if eid == prefix or eid.startswith(prefix + "."):
            return label
    return "Framework"


def _node(
    *,
    node_id: str,
    kind: str,
    pref_label: str,
    source_id: str,
    alt_labels: list[str] | None = None,
    description: str = "",
    code: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "source": SOURCE,
        "source_id": source_id,
        "pref_label": pref_label or "",
        "alt_labels": list(alt_labels or []),
        "description": description or "",
        "code": code,
        "kind": kind,
        "extra": dict(extra or {}),
    }


def _merge_nodes(session: Session, label: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    # MERGE on the umbrella so a Content-Model id can gain extra labels
    # (IWA/DWA) without creating a second node with the same id.
    extra_label = "" if label == LABEL_ONET_NODE else f"SET n:{label}"
    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{LABEL_ONET_NODE} {{id: row.id}})
    {extra_label}
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


def _merge_rel_count(
    session: Session,
    cypher: str,
    rows: list[dict[str, Any]],
    *,
    relationship: str,
) -> int:
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


def _rel_cypher(rel: str, extra_set: str = "") -> str:
    setter = f"SET {extra_set}" if extra_set else ""
    return f"""
    UNWIND $rows AS row
    MATCH (a:{LABEL_ONET_NODE} {{id: row.from_id}})
    MATCH (b:{LABEL_ONET_NODE} {{id: row.to_id}})
    MERGE (a)-[r:{rel}]->(b)
    {setter}
    RETURN count(*) AS c
    """


def _rel_cypher_keyed(rel: str, keys: tuple[str, ...], extra_set: str) -> str:
    props = ", ".join(f"{k}: row.{k}" for k in keys)
    return f"""
    UNWIND $rows AS row
    MATCH (a:{LABEL_ONET_NODE} {{id: row.from_id}})
    MATCH (b:{LABEL_ONET_NODE} {{id: row.to_id}})
    MERGE (a)-[r:{rel} {{{props}}}]->(b)
    SET {extra_set}
    RETURN count(*) AS c
    """


def wipe_onet_graph(session: Session) -> None:
    """Delete O*NET suite nodes only (never the whole database / ESCO)."""
    session.run(
        f"MATCH (n:{LABEL_ONET_NODE}) WHERE n.source = $source DETACH DELETE n",
        source=SOURCE,
    )


def _add_unique_node(bucket: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    existing = bucket.get(row["id"])
    if existing is None:
        bucket[row["id"]] = row
        return
    labels = list(dict.fromkeys([*existing["alt_labels"], *row["alt_labels"]]))
    existing["alt_labels"] = labels
    if not existing["description"] and row["description"]:
        existing["description"] = row["description"]
    extra = dict(existing.get("extra") or {})
    extra.update(row.get("extra") or {})
    existing["extra"] = extra


def _pair_im_lv(
    rows: Iterable[Mapping[str, Any]],
    *,
    relation_type: str | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        soc = _cell(row, "O*NET-SOC Code")
        eid = _cell(row, "Element ID")
        scale = _cell(row, "Scale ID")
        if not soc or not eid or not scale:
            continue
        grouped[(soc, eid)][scale] = row
    edges: list[dict[str, Any]] = []
    for (soc, eid), scales in grouped.items():
        im = scales.get("IM", {})
        lv = scales.get("LV", {})
        sample = im or lv
        edge = {
            "from_id": suite_id_occupation(soc),
            "to_id": suite_id_element(eid),
            "importance": _num(im.get("Data Value") if im else None),
            "level": _num(lv.get("Data Value") if lv else None),
            "n": _num(sample.get("N")),
            "standard_error": _num(sample.get("Standard Error")),
            "ci_lower": _num(sample.get("Lower CI Bound")),
            "ci_upper": _num(sample.get("Upper CI Bound")),
            "recommend_suppress": _cell(sample, "Recommend Suppress") or None,
        }
        if relation_type:
            edge["relation_type"] = relation_type
        edges.append(edge)
    return edges


def _rowwise_rating_edges(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for row in rows:
        soc = _cell(row, "O*NET-SOC Code")
        eid = _cell(row, "Element ID")
        if not soc or not eid:
            continue
        category = _cell(row, "Category") or "n/a"
        edges.append(
            {
                "from_id": suite_id_occupation(soc),
                "to_id": suite_id_element(eid),
                "scale_id": _cell(row, "Scale ID") or "",
                "category": category,
                "data_value": _num(row.get("Data Value")),
                "n": _num(row.get("N")),
                "standard_error": _num(row.get("Standard Error")),
                "ci_lower": _num(row.get("Lower CI Bound")),
                "ci_upper": _num(row.get("Upper CI Bound")),
                "recommend_suppress": _cell(row, "Recommend Suppress") or None,
            }
        )
    return edges


def normalize_document(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Translate fixture JSON or full TSV-shaped dict into MERGE payloads."""
    nodes: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def put(label: str, row: dict[str, Any]) -> None:
        _add_unique_node(nodes[label], row)

    for row in doc.get("content_model_reference") or []:
        eid = _cell(row, "Element ID")
        if not eid:
            continue
        kind = kind_for_element_id(eid)
        put(
            kind,
            _node(
                node_id=suite_id_element(eid),
                kind=kind,
                pref_label=_cell(row, "Element Name"),
                source_id=eid,
                description=_cell(row, "Description"),
                code=eid,
            ),
        )
    for row in doc.get("career_interest_type_keywords") or []:
        eid = _cell(row, "Element ID")
        keyword = _cell(row, "Keyword")
        node = nodes[LABEL_INTEREST].get(suite_id_element(eid)) if eid else None
        if node is not None and keyword:
            node["alt_labels"] = list(dict.fromkeys([*node["alt_labels"], keyword]))

    alt_by_soc: dict[str, list[str]] = defaultdict(list)
    for row in doc.get("job_titles") or []:
        soc = _cell(row, "O*NET-SOC Code")
        title = _cell(row, "Job Title")
        if soc and title:
            alt_by_soc[soc].append(title)
    for row in doc.get("sample_of_reported_titles") or []:
        soc = _cell(row, "O*NET-SOC Code")
        title = _cell(row, "Reported Job Title")
        if soc and title:
            alt_by_soc[soc].append(title)

    for row in doc.get("occupation_data") or []:
        soc = _cell(row, "O*NET-SOC Code")
        put(
            LABEL_OCCUPATION,
            _node(
                node_id=suite_id_occupation(soc),
                kind=LABEL_OCCUPATION,
                pref_label=_cell(row, "Title"),
                source_id=soc,
                alt_labels=list(dict.fromkeys(alt_by_soc.get(soc, []))),
                description=_cell(row, "Description"),
                code=soc,
            ),
        )

    for row in doc.get("task_statements") or []:
        tid = _cell(row, "Task ID")
        put(
            LABEL_TASK,
            _node(
                node_id=suite_id_task(tid),
                kind=LABEL_TASK,
                pref_label=_cell(row, "Task"),
                source_id=tid,
                extra={"task_type": _cell(row, "Task Type") or None},
            ),
        )

    for index, row in enumerate(doc.get("emerging_tasks") or []):
        soc = _cell(row, "O*NET-SOC Code")
        original = _cell(row, "Original Task ID")
        task_text = _cell(row, "Task")
        if original and original.lower() != "n/a":
            tid = original
        else:
            try:
                tid = f"emerging-{soc}-{software_slug(task_text)[:60]}"
            except OnetIdError:
                tid = f"emerging-{soc}-{index}"
        put(
            LABEL_TASK,
            _node(
                node_id=suite_id_task(tid),
                kind=LABEL_TASK,
                pref_label=task_text,
                source_id=tid,
                extra={"task_type": "emerging", "emerging_category": _cell(row, "Category")},
            ),
        )

    software_names: dict[str, str] = {}
    for row in doc.get("software_skills") or []:
        name = _cell(row, "Workplace Example")
        if not name:
            continue
        slug = software_slug(name)
        previous = software_names.get(slug)
        if previous is not None and previous != name:
            raise OnetLoadValidationError(
                f"software slug {slug!r} collides: {previous!r} vs {name!r}"
            )
        software_names[slug] = name
        put(
            LABEL_SOFTWARE,
            _node(
                node_id=suite_id_software(name),
                kind=LABEL_SOFTWARE,
                pref_label=name,
                source_id=name,
                extra={
                    "element_id": _cell(row, "Element ID") or None,
                    "element_name": _cell(row, "Element Name") or None,
                },
            ),
        )

    for row in doc.get("job_zone_reference") or []:
        zone = _cell(row, "Job Zone")
        put(
            LABEL_JOB_ZONE,
            _node(
                node_id=suite_id_job_zone(zone),
                kind=LABEL_JOB_ZONE,
                pref_label=_cell(row, "Name") or f"Job Zone {zone}",
                source_id=zone,
                description=_cell(row, "Education"),
                extra={
                    "experience": _cell(row, "Experience") or None,
                    "job_training": _cell(row, "Job Training") or None,
                    "svp_range": _cell(row, "SVP Range") or None,
                },
            ),
        )
    for row in doc.get("job_zones") or []:
        zone = _cell(row, "Job Zone")
        put(
            LABEL_JOB_ZONE,
            _node(
                node_id=suite_id_job_zone(zone),
                kind=LABEL_JOB_ZONE,
                pref_label=f"Job Zone {zone}",
                source_id=zone,
            ),
        )

    for row in doc.get("scales_reference") or []:
        sid = _cell(row, "Scale ID")
        put(
            LABEL_SCALE,
            _node(
                node_id=suite_id_scale(sid),
                kind=LABEL_SCALE,
                pref_label=_cell(row, "Scale Name") or sid,
                source_id=sid,
                extra={
                    "minimum": _num(row.get("Minimum")),
                    "maximum": _num(row.get("Maximum")),
                },
            ),
        )

    for row in doc.get("gwas_to_iwas") or []:
        iwa = _cell(row, "IWA Element ID")
        put(
            LABEL_INTERMEDIATE_WORK_ACTIVITY,
            _node(
                node_id=suite_id_element(iwa),
                kind=LABEL_INTERMEDIATE_WORK_ACTIVITY,
                pref_label=_cell(row, "IWA Element Name") or iwa,
                source_id=iwa,
                code=iwa,
            ),
        )
    for row in doc.get("gwas_to_iwas_to_dwas") or []:
        dwa = _cell(row, "DWA Element ID")
        put(
            LABEL_DETAILED_WORK_ACTIVITY,
            _node(
                node_id=suite_id_element(dwa),
                kind=LABEL_DETAILED_WORK_ACTIVITY,
                pref_label=_cell(row, "DWA Element Name") or dwa,
                source_id=dwa,
                code=dwa,
            ),
        )

    performs = [
        {
            "from_id": suite_id_occupation(_cell(r, "O*NET-SOC Code")),
            "to_id": suite_id_task(_cell(r, "Task ID")),
            "task_type": _cell(r, "Task Type") or None,
        }
        for r in doc.get("task_statements") or []
        if _cell(r, "O*NET-SOC Code") and _cell(r, "Task ID")
    ]
    for index, row in enumerate(doc.get("emerging_tasks") or []):
        soc = _cell(row, "O*NET-SOC Code")
        original = _cell(row, "Original Task ID")
        task_text = _cell(row, "Task")
        if original and original.lower() != "n/a":
            tid = original
        else:
            try:
                tid = f"emerging-{soc}-{software_slug(task_text)[:60]}"
            except OnetIdError:
                tid = f"emerging-{soc}-{index}"
        performs.append(
            {
                "from_id": suite_id_occupation(soc),
                "to_id": suite_id_task(tid),
                "task_type": "emerging",
            }
        )

    uses_software = [
        {
            "from_id": suite_id_occupation(_cell(r, "O*NET-SOC Code")),
            "to_id": suite_id_software(_cell(r, "Workplace Example")),
            "hot_technology": _cell(r, "Hot Technology") or None,
            "in_demand": _cell(r, "In Demand") or None,
            "element_id": _cell(r, "Element ID") or None,
        }
        for r in doc.get("software_skills") or []
        if _cell(r, "O*NET-SOC Code") and _cell(r, "Workplace Example")
    ]

    related = [
        {
            "from_id": suite_id_occupation(_cell(r, "O*NET-SOC Code")),
            "to_id": suite_id_occupation(_cell(r, "Related O*NET-SOC Code")),
            "relatedness_tier": _cell(r, "Relatedness Tier") or None,
            "index": _num(r.get("Index")),
        }
        for r in doc.get("related_occupations") or []
        if _cell(r, "O*NET-SOC Code") and _cell(r, "Related O*NET-SOC Code")
    ]

    has_zone = [
        {
            "from_id": suite_id_occupation(_cell(r, "O*NET-SOC Code")),
            "to_id": suite_id_job_zone(_cell(r, "Job Zone")),
        }
        for r in doc.get("job_zones") or []
        if _cell(r, "O*NET-SOC Code") and _cell(r, "Job Zone")
    ]

    broader: list[dict[str, Any]] = []
    for row in doc.get("gwas_to_iwas") or []:
        broader.append(
            {
                "from_id": suite_id_element(_cell(row, "IWA Element ID")),
                "to_id": suite_id_element(_cell(row, "GWA Element ID")),
            }
        )
    for row in doc.get("gwas_to_iwas_to_dwas") or []:
        broader.append(
            {
                "from_id": suite_id_element(_cell(row, "DWA Element ID")),
                "to_id": suite_id_element(_cell(row, "IWA Element ID")),
            }
        )

    related_elements: list[dict[str, Any]] = [
        {
            "from_id": suite_id_task(_cell(r, "Task ID")),
            "to_id": suite_id_element(_cell(r, "DWA Element ID")),
        }
        for r in doc.get("tasks_to_dwas") or []
        if _cell(r, "Task ID") and _cell(r, "DWA Element ID")
    ]
    related_elements.extend(
        {
            "from_id": suite_id_element(_cell(r, "Specific Interest Areas Element ID")),
            "to_id": suite_id_element(_cell(r, "Career Interest Types Element ID")),
        }
        for r in doc.get("specific_interest_areas_to_career_interest_types") or []
        if _cell(r, "Specific Interest Areas Element ID")
        and _cell(r, "Career Interest Types Element ID")
    )

    def _cross(rows: Iterable[Mapping[str, Any]], from_key: str, to_key: str) -> None:
        for row in rows:
            left, right = _cell(row, from_key), _cell(row, to_key)
            if left and right:
                related_elements.append(
                    {"from_id": suite_id_element(left), "to_id": suite_id_element(right)}
                )

    _cross(
        doc.get("abilities_to_work_activities") or [],
        "Abilities Element ID",
        "Work Activities Element ID",
    )
    _cross(
        doc.get("abilities_to_work_context") or [],
        "Abilities Element ID",
        "Work Context Element ID",
    )
    _cross(
        doc.get("essential_skills_to_work_activities") or [],
        "Essential Skills Element ID",
        "Work Activities Element ID",
    )
    _cross(
        doc.get("essential_skills_to_work_context") or [],
        "Essential Skills Element ID",
        "Work Context Element ID",
    )
    _cross(
        doc.get("transferable_skills_to_work_activities") or [],
        "Transferable Skills Element ID",
        "Work Activities Element ID",
    )
    _cross(
        doc.get("transferable_skills_to_work_context") or [],
        "Transferable Skills Element ID",
        "Work Context Element ID",
    )
    _cross(
        doc.get("work_styles_to_work_activities") or [],
        "Work Styles Element ID",
        "Work Activities Element ID",
    )
    _cross(
        doc.get("work_styles_to_work_context") or [],
        "Work Styles Element ID",
        "Work Context Element ID",
    )
    for row in doc.get("cross_domain") or []:
        ids = [str(v).strip() for k, v in row.items() if str(k).endswith("Element ID") and v]
        if len(ids) >= 2:
            related_elements.append(
                {"from_id": suite_id_element(ids[0]), "to_id": suite_id_element(ids[1])}
            )

    has_interest = _rowwise_rating_edges(
        list(doc.get("career_interest_types") or [])
        + list(doc.get("specific_interest_areas") or [])
    )
    has_education = _rowwise_rating_edges(doc.get("education") or [])
    has_training = _rowwise_rating_edges(doc.get("training_and_experience") or [])
    has_work_context = _rowwise_rating_edges(doc.get("work_context") or [])
    has_work_style = _rowwise_rating_edges(doc.get("work_styles") or [])

    known_ids = {row["id"] for bucket in nodes.values() for row in bucket.values()}

    def _known(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for edge in edges:
            if edge["from_id"] in known_ids and edge["to_id"] in known_ids:
                kept.append(edge)
        return kept

    return {
        "occupations": list(nodes[LABEL_OCCUPATION].values()),
        "skills": list(nodes[LABEL_SKILL].values()),
        "knowledge": list(nodes[LABEL_KNOWLEDGE].values()),
        "abilities": list(nodes[LABEL_ABILITY].values()),
        "work_activities": list(nodes[LABEL_WORK_ACTIVITY].values()),
        "work_context": list(nodes[LABEL_WORK_CONTEXT].values()),
        "work_styles": list(nodes[LABEL_WORK_STYLE].values()),
        "tasks": list(nodes[LABEL_TASK].values()),
        "software": list(nodes[LABEL_SOFTWARE].values()),
        "job_zones": list(nodes[LABEL_JOB_ZONE].values()),
        "interests": list(nodes[LABEL_INTEREST].values()),
        "scales": list(nodes[LABEL_SCALE].values()),
        "iwas": list(nodes[LABEL_INTERMEDIATE_WORK_ACTIVITY].values()),
        "dwas": list(nodes[LABEL_DETAILED_WORK_ACTIVITY].values()),
        "frameworks": list(nodes.get("Framework", {}).values()),
        "has_skill": _known(
            _pair_im_lv(doc.get("essential_skills") or [], relation_type="essential")
            + _pair_im_lv(doc.get("transferable_skills") or [], relation_type="transferable")
        ),
        "has_knowledge": _known(_pair_im_lv(doc.get("knowledge") or [])),
        "has_ability": _known(_pair_im_lv(doc.get("abilities") or [])),
        "has_work_activity": _known(_pair_im_lv(doc.get("work_activities") or [])),
        "has_work_context": _known(has_work_context),
        "has_work_style": _known(has_work_style),
        "performs_task": _known(performs),
        "uses_software": _known(uses_software),
        "related_to": _known(related),
        "related_elements": _known(related_elements),
        "broader_than": _known(broader),
        "has_job_zone": _known(has_zone),
        "has_interest": _known(has_interest),
        "has_education": _known(has_education),
        "has_training": _known(has_training),
        "task_ratings": _known(
            [
                {
                    "from_id": suite_id_occupation(_cell(r, "O*NET-SOC Code")),
                    "to_id": suite_id_task(_cell(r, "Task ID")),
                    "scale_id": _cell(r, "Scale ID") or "",
                    "category": _cell(r, "Category") or "n/a",
                    "data_value": _num(r.get("Data Value")),
                    "n": _num(r.get("N")),
                    "standard_error": _num(r.get("Standard Error")),
                    "ci_lower": _num(r.get("Lower CI Bound")),
                    "ci_upper": _num(r.get("Upper CI Bound")),
                    "recommend_suppress": _cell(r, "Recommend Suppress") or None,
                }
                for r in doc.get("task_ratings") or []
                if _cell(r, "O*NET-SOC Code") and _cell(r, "Task ID")
            ]
        ),
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_full_document(data_dir: Path) -> dict[str, Any]:
    doc: dict[str, Any] = {"meta": {"suite": SOURCE, "mode": "full", "data_dir": str(data_dir)}}
    missing = [name for name in REQUIRED_FILES.values() if not (data_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing O*NET tables: "
            + ", ".join(missing)
            + f". Download {DOWNLOAD_URL} into {data_dir}"
        )
    for key, filename in REQUIRED_FILES.items():
        path = data_dir / filename
        print(f"  reading {filename} …", flush=True)
        doc[key] = _read_tsv(path)
        print(f"    → {len(doc[key]):,} rows", flush=True)
    return doc


def _dedupe_edges(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        seen[tuple(row.get(k) for k in keys)] = row
    return list(seen.values())


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
        node_steps: list[tuple[str, str]] = [
            ("occupations", LABEL_OCCUPATION),
            ("skills", LABEL_SKILL),
            ("knowledge", LABEL_KNOWLEDGE),
            ("abilities", LABEL_ABILITY),
            ("work_activities", LABEL_WORK_ACTIVITY),
            ("work_context", LABEL_WORK_CONTEXT),
            ("work_styles", LABEL_WORK_STYLE),
            ("tasks", LABEL_TASK),
            ("software", LABEL_SOFTWARE),
            ("job_zones", LABEL_JOB_ZONE),
            ("interests", LABEL_INTEREST),
            ("scales", LABEL_SCALE),
            ("iwas", LABEL_INTERMEDIATE_WORK_ACTIVITY),
            ("dwas", LABEL_DETAILED_WORK_ACTIVITY),
            ("frameworks", LABEL_ONET_NODE),
        ]
        for key, label in node_steps:
            print(f"  merging {key} …", flush=True)
            counts[key] = _merge_nodes(session, label, payload.get(key) or [])
            print(f"    → {counts[key]:,}", flush=True)

        weighted_set = (
            "r.importance = row.importance, r.level = row.level, r.n = row.n, "
            "r.standard_error = row.standard_error, r.ci_lower = row.ci_lower, "
            "r.ci_upper = row.ci_upper, r.recommend_suppress = row.recommend_suppress"
        )
        skill_set = weighted_set + ", r.relation_type = row.relation_type"
        rated_set = (
            "r.data_value = row.data_value, r.n = row.n, r.standard_error = row.standard_error, "
            "r.ci_lower = row.ci_lower, r.ci_upper = row.ci_upper, "
            "r.recommend_suppress = row.recommend_suppress"
        )
        rel_steps: list[tuple[str, str, str, tuple[str, ...] | None]] = [
            ("has_skill", REL_HAS_SKILL, skill_set, None),
            ("has_knowledge", REL_HAS_KNOWLEDGE, weighted_set, None),
            ("has_ability", REL_HAS_ABILITY, weighted_set, None),
            ("has_work_activity", REL_HAS_WORK_ACTIVITY, weighted_set, None),
            ("has_work_context", REL_HAS_WORK_CONTEXT, rated_set, ("scale_id", "category")),
            ("has_work_style", REL_HAS_WORK_STYLE, rated_set, ("scale_id", "category")),
            ("performs_task", REL_PERFORMS_TASK, "r.task_type = row.task_type", None),
            (
                "uses_software",
                REL_USES_SOFTWARE,
                "r.hot_technology = row.hot_technology, r.in_demand = row.in_demand, "
                "r.element_id = row.element_id",
                None,
            ),
            (
                "related_to",
                REL_RELATED_TO,
                "r.relatedness_tier = row.relatedness_tier, r.index = row.index",
                None,
            ),
            ("related_elements", REL_RELATED_TO, "", None),
            ("broader_than", REL_BROADER_THAN, "", None),
            ("has_job_zone", REL_HAS_JOB_ZONE, "", None),
            ("has_interest", REL_HAS_INTEREST, rated_set, ("scale_id", "category")),
            ("has_education", REL_HAS_EDUCATION, rated_set, ("scale_id", "category")),
            ("has_training", REL_HAS_TRAINING, rated_set, ("scale_id", "category")),
            ("task_ratings", REL_HAS_TASK_RATING, rated_set, ("scale_id", "category")),
        ]
        for key, rel, setter, keys in rel_steps:
            rows = payload.get(key) or []
            print(f"  merging {key} …", flush=True)
            if keys:
                cypher = _rel_cypher_keyed(rel, keys, setter)
            else:
                cypher = _rel_cypher(rel, setter)
            counts[key] = _merge_rel_count(session, cypher, rows, relationship=rel)
            print(f"    → {counts[key]:,}", flush=True)
    return counts


def validate_load(
    driver: Driver,
    expected: Mapping[str, int],
    *,
    database: str | None = None,
) -> dict[str, int]:
    with driver.session(database=database) as session:

        def count_label(label: str) -> int:
            rec = session.run(
                f"MATCH (n:{label}) WHERE n.source = $source RETURN count(n) AS c",
                source=SOURCE,
            ).single()
            return int(rec["c"]) if rec else 0

        live = {
            "occupations": count_label(LABEL_OCCUPATION),
            "skills": count_label(LABEL_SKILL),
            "tasks": count_label(LABEL_TASK),
            "software": count_label(LABEL_SOFTWARE),
        }
        dangling = session.run(
            f"""
            MATCH (o:{LABEL_OCCUPATION} {{source: $source}})-[r:{REL_HAS_SKILL}]->(s)
            WHERE NOT s:{LABEL_ONET_NODE}
            RETURN count(r) AS c
            """,
            source=SOURCE,
        ).single()
        dangling_n = int(dangling["c"]) if dangling else 0
        blank = session.run(
            f"""
            MATCH (n:{LABEL_ONET_NODE})
            WHERE n.source = $source AND (n.id IS NULL OR n.id = '')
            RETURN count(n) AS c
            """,
            source=SOURCE,
        ).single()
        blank_n = int(blank["c"]) if blank else 0

    for name, expected_count in expected.items():
        actual = live.get(name)
        if actual is None:
            continue
        if actual != expected_count:
            raise OnetLoadValidationError(
                f"O*NET {name} count mismatch: expected {expected_count}, found {actual}"
            )
    if dangling_n:
        raise OnetLoadValidationError(f"dangling HAS_SKILL edges: {dangling_n}")
    if blank_n:
        raise OnetLoadValidationError(f"blank identity nodes: {blank_n}")
    return live


def run_load(
    mode: str = "fixture",
    *,
    data_dir: Path | None = None,
    fixture_path: Path | None = None,
    wipe: bool = True,
) -> dict[str, int]:
    if mode == "fixture":
        doc = load_fixture_document(fixture_path)
    elif mode == "full":
        root = data_dir or Path(os.getenv("ONET_DATA_DIR", "data/onet/raw"))
        doc = load_full_document(root)
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(f"Normalizing ({mode}) …", flush=True)
    payload = normalize_document(doc)
    edge_keys = {
        "has_skill": ("from_id", "to_id", "relation_type"),
        "has_knowledge": ("from_id", "to_id"),
        "has_ability": ("from_id", "to_id"),
        "has_work_activity": ("from_id", "to_id"),
        "has_work_context": ("from_id", "to_id", "scale_id", "category"),
        "has_work_style": ("from_id", "to_id", "scale_id", "category"),
        "performs_task": ("from_id", "to_id"),
        "uses_software": ("from_id", "to_id"),
        "related_to": ("from_id", "to_id"),
        "related_elements": ("from_id", "to_id"),
        "broader_than": ("from_id", "to_id"),
        "has_job_zone": ("from_id", "to_id"),
        "has_interest": ("from_id", "to_id", "scale_id", "category"),
        "has_education": ("from_id", "to_id", "scale_id", "category"),
        "has_training": ("from_id", "to_id", "scale_id", "category"),
        "task_ratings": ("from_id", "to_id", "scale_id", "category"),
    }
    for key, keys in edge_keys.items():
        payload[key] = _dedupe_edges(payload.get(key) or [], keys)

    print(
        f"  occupations={len(payload['occupations']):,} "
        f"skills={len(payload['skills']):,} "
        f"tasks={len(payload['tasks']):,} "
        f"software={len(payload['software']):,} "
        f"has_skill={len(payload['has_skill']):,}",
        flush=True,
    )

    with neo4j_driver() as (driver, database):
        verify_connectivity(driver)
        print("Connected to Neo4j. Loading …", flush=True)
        counts = load_normalized(driver, payload, database=database, wipe=wipe)
        expected = {
            "occupations": len(payload["occupations"]),
            "skills": len(payload["skills"]),
            "tasks": len(payload["tasks"]),
            "software": len(payload["software"]),
        }
        print("Validating …", flush=True)
        live = validate_load(driver, expected, database=database)
        live.update({k: counts[k] for k in counts if k not in live})
    return live


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load O*NET suite into Neo4j")
    parser.add_argument(
        "--mode",
        choices=("fixture", "full"),
        default="fixture",
        help="fixture = committed subset; full = data/onet/raw TSVs",
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="Override ONET_DATA_DIR")
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Do not DETACH DELETE existing O*NET nodes before load",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    live = run_load(mode=args.mode, data_dir=args.data_dir, wipe=not args.no_wipe)
    print(json.dumps({"ok": True, "mode": args.mode, "counts": live}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
