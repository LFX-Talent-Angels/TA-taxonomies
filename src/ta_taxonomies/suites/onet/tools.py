"""O*NET suite tools: query the loaded graph via the shared suite contract.

Use case: after load.py has populated Neo4j, callers use ``OnetSuite`` for
Locate/Connect/Pathfind-style operations. Returns contract ``ToolResult``
models, not raw Neo4j records.

Locate retrieval copies ESCO/Albedo: range index for exact preferred label,
full-text for alias/casefold/contains, original Cypher predicate re-applied,
Lucene scores never become confidence. ``score_paths`` implements named
policy ``onet-importance-v1`` (mean of ``HAS_SKILL.importance`` on a path).
"""

from __future__ import annotations

import re
from typing import Any

from neo4j import Driver, Session

from ta_taxonomies.contract.models import (
    Edge,
    Node,
    Path,
    PolicyRef,
    PruningStats,
    ScoredPath,
    ToolResult,
)
from ta_taxonomies.contract.schema import SuiteSchema
from ta_taxonomies.suites._locate import (
    Confidences,
    GroupScheme,
    LocateConfig,
    Locator,
    embed_query,
    locate_result,
    lucene_infix,
    lucene_phrase,
    queries_for,
    query_terms,
    record_to_node,
)
from ta_taxonomies.suites.onet.config import (
    CONF_CASEFOLD_AMBIGUOUS,
    CONF_CASEFOLD_UNIQUE,
    CONF_CONTAINS,
    CONF_EXACT_ALT,
    CONF_EXACT_PREF,
    CONF_HYBRID,
    FULLTEXT_INDEX,
    KIND_ALIASES,
    KIND_EXPANSIONS,
    LABEL_ABILITY,
    LABEL_INTEREST,
    LABEL_KNOWLEDGE,
    LABEL_OCCUPATION,
    LABEL_ONET_NODE,
    LABEL_SKILL,
    LABEL_SOFTWARE,
    LABEL_TASK,
    LABEL_WORK_ACTIVITY,
    MAX_BRANCHING,
    MAX_FRONTIER_PATHS,
    MAX_PATH_DEPTH,
    MAX_PATHS,
    MIN_WILDCARD_TERM,
    SEARCH_LIMIT,
    SEARCH_SCAN_CAP,
    SOC_MAJOR_GROUPS,
    SOURCE,
    TRAVERSABLE_RELS,
)

IMPORTANCE_POLICY = PolicyRef(name="onet-importance-v1", version="1")


# Everything O*NET's Locate needs; the tiers themselves live in suites._locate.
_SOC_CODE = re.compile(r"[0-9]{2}-[0-9]{4}(?:\.[0-9]{2})?")


def _onet_codes(q: str) -> list[str]:
    if not _SOC_CODE.fullmatch(q):
        return []
    return [q] if "." in q else [f"{q}.00"]


def _soc_names(_session: Session, codes: list[str]) -> dict[str, dict[str, Any]]:
    return {code: {"label": SOC_MAJOR_GROUPS.get(code)} for code in codes}


ONET_LOCATE = LocateConfig(
    source=SOURCE,
    node_label=LABEL_ONET_NODE,
    occupation_label=LABEL_OCCUPATION,
    default_labels=(
        LABEL_OCCUPATION,
        LABEL_SKILL,
        LABEL_TASK,
        LABEL_SOFTWARE,
        LABEL_KNOWLEDGE,
        LABEL_ABILITY,
        LABEL_WORK_ACTIVITY,
        LABEL_INTEREST,
    ),
    kind_aliases=KIND_ALIASES,
    kind_expansions=KIND_EXPANSIONS,
    fulltext_index=FULLTEXT_INDEX,
    vector_index="onet_label_embedding",
    confidences=Confidences(
        exact_pref=CONF_EXACT_PREF,
        exact_alt=CONF_EXACT_ALT,
        casefold_unique=CONF_CASEFOLD_UNIQUE,
        casefold_ambiguous=CONF_CASEFOLD_AMBIGUOUS,
        contains=CONF_CONTAINS,
        hybrid=CONF_HYBRID,
    ),
    # Never fall back to the graph id: onet:occupation:<code> is not a native
    # source id, and memory writes would double the prefix (onet:onet:...).
    source_id_fallbacks=(),
    groups=GroupScheme(
        name="soc-2018-major",
        code_expr="CASE WHEN 'Occupation' IN labels(n) THEN substring(n.code, 0, 2) END",
        member_expr="n.code",
        prefix_for=lambda code: f"{code}-" if code in SOC_MAJOR_GROUPS else None,
        names_for=_soc_names,
    ),
    # O*NET-SOC codes; "15-1252" means the base occupation "15-1252.00".
    codes_for=_onet_codes,
    search_limit=SEARCH_LIMIT,
    scan_cap=SEARCH_SCAN_CAP,
    min_wildcard_term=MIN_WILDCARD_TERM,
)


# Names kept for callers and tests that import them from this module.
_SEARCHABLE_LABELS = ONET_LOCATE.searchable_labels


def _query_terms(q: str) -> list[str]:
    return query_terms(q)


def _lucene_phrase(q: str) -> str | None:
    return lucene_phrase(q)


def _lucene_infix(q: str) -> str | None:
    return lucene_infix(q, ONET_LOCATE.min_wildcard_term)


def _exact_pref_cypher(labels: list[str]) -> str:
    return queries_for(ONET_LOCATE).exact_pref(labels)


_SCAN_CONTAINS = queries_for(ONET_LOCATE).scan_contains
_FULLTEXT_CONTAINS = queries_for(ONET_LOCATE).fulltext_contains


def _embed_query(text: str) -> list[float] | None:
    """The suite's meaning-search hook; tests replace it to avoid the model."""
    return embed_query(text)


def _record_to_node(rec: dict[str, Any]) -> Node:
    return record_to_node(ONET_LOCATE, rec)


def _locate_result(
    rows: list[dict[str, Any]],
    total: int,
    confidence: float,
    method: str,
    evidence_suffix: str,
    notes: list[str],
    *,
    ambiguous: bool = False,
) -> ToolResult:
    return locate_result(
        ONET_LOCATE, rows, total, confidence, method, evidence_suffix, notes, ambiguous=ambiguous
    )


def _fetch_by_ids(session: Session, ids: list[str]) -> dict[str, Node]:
    if not ids:
        return {}
    result = session.run(
        f"""
        MATCH (n:{LABEL_ONET_NODE})
        WHERE n.source = $source AND n.id IN $ids
        RETURN n.id AS id, n.pref_label AS pref_label, n.source AS source,
               n.source_id AS source_id, n.kind AS kind,
               n.code AS code, n.description AS description,
               n.alt_labels AS alt_labels, labels(n) AS labels
        """,
        ids=ids,
        source=SOURCE,
    )
    out: dict[str, Node] = {}
    for rec in result:
        node = _record_to_node(dict(rec))
        out[node.id] = node
    return out


def _importance_values(path: Path) -> list[float]:
    values: list[float] = []
    for edge in path.edges:
        raw = (edge.properties or {}).get("importance")
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            values.append(float(raw))
    return values


class OnetSuite:
    """O*NET implementation of the suite contract (read path against Neo4j)."""

    name = SOURCE

    def __init__(self, driver: Driver, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    def _session(self) -> Session:
        return self._driver.session(database=self._database)

    @property
    def suite_schema(self) -> SuiteSchema:
        return SuiteSchema(
            skill_rel_types=("HAS_SKILL", "USES_SOFTWARE"),
            optional_rel_values=frozenset({"optional", "transferable"}),
            group_rel_type=None,
            group_node_kinds=frozenset(),
        )

    def _locator(self) -> Locator:
        return Locator(ONET_LOCATE, self._session, lambda q: _embed_query(q))

    def search_nodes(self, text: str, kind: str | None = None) -> ToolResult:
        """Locate: free text to O*NET nodes with confidence (see ``suites._locate``)."""
        return self._locator().search_nodes(text, kind)

    def search_group(self, text: str, group: str) -> ToolResult:
        """Occupations matching ``text`` inside one SOC major group from ``meta.groups``."""
        return self._locator().search_group(text, group)

    def get_neighbors(
        self,
        node_id: str,
        rel_types: list[str] | None = None,
    ) -> ToolResult:
        allowed = TRAVERSABLE_RELS
        if rel_types:
            requested = {r for r in rel_types}
            bad = requested - allowed
            if bad:
                return ToolResult(warnings=[f"unknown_rel_types:{sorted(bad)}"])
            types = sorted(requested)
        else:
            types = sorted(allowed)

        with self._session() as session:
            result = session.run(
                f"""
                MATCH (a:{LABEL_ONET_NODE} {{id: $id}})-[r]->(b:{LABEL_ONET_NODE})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN a.id AS from_id, b.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props,
                       b.pref_label AS pref_label, b.source AS source,
                       b.source_id AS source_id, b.kind AS kind,
                       b.code AS code, b.description AS description,
                       b.alt_labels AS alt_labels, labels(b) AS labels
                UNION
                MATCH (b:{LABEL_ONET_NODE})-[r]->(a:{LABEL_ONET_NODE} {{id: $id}})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN b.id AS from_id, a.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props,
                       b.pref_label AS pref_label, b.source AS source,
                       b.source_id AS source_id, b.kind AS kind,
                       b.code AS code, b.description AS description,
                       b.alt_labels AS alt_labels, labels(b) AS labels
                """,
                id=node_id,
                types=types,
                source=SOURCE,
            )
            rows = [dict(r) for r in result]
            rows.sort(key=lambda row: (row["rel_type"], row["from_id"], row["to_id"]))
            if not rows:
                exists = session.run(
                    f"MATCH (n:{LABEL_ONET_NODE} {{id: $id}}) "
                    "WHERE n.source = $source RETURN n.id AS id",
                    id=node_id,
                    source=SOURCE,
                ).single()
                if not exists:
                    return ToolResult(warnings=["node_not_found"])
                return ToolResult(warnings=["no_neighbors"], nodes=[])

            nodes_map: dict[str, Node] = {}
            edges: list[Edge] = []
            center = _fetch_by_ids(session, [node_id]).get(node_id)
            if center:
                nodes_map[node_id] = center

            for r in rows:
                neighbor_id = r["to_id"] if r["from_id"] == node_id else r["from_id"]
                nodes_map[neighbor_id] = _record_to_node(
                    {
                        "id": neighbor_id,
                        "pref_label": r.get("pref_label"),
                        "source": r.get("source"),
                        "source_id": r.get("source_id"),
                        "kind": r.get("kind"),
                        "code": r.get("code"),
                        "description": r.get("description"),
                        "alt_labels": r.get("alt_labels"),
                        "labels": r.get("labels"),
                    }
                )
                edges.append(
                    Edge(
                        type=r["rel_type"],
                        from_id=r["from_id"],
                        to_id=r["to_id"],
                        properties=dict(r.get("rel_props") or {}),
                    )
                )

            return ToolResult(
                nodes=list(nodes_map.values()),
                edges=edges,
                evidence=[f"onet:neighbors:{node_id}"],
            )

    def enumerate_paths(
        self,
        from_id: str,
        to_id: str,
        *,
        max_depth: int = 4,
        max_paths: int = 20,
    ) -> ToolResult:
        if max_depth < 1 or max_depth > MAX_PATH_DEPTH:
            return ToolResult(warnings=["invalid_max_depth"])
        if max_paths < 1 or max_paths > MAX_PATHS:
            return ToolResult(warnings=["invalid_max_paths"])

        with self._session() as session:
            ends = _fetch_by_ids(session, [from_id, to_id])
            if from_id not in ends or to_id not in ends:
                return ToolResult(warnings=["endpoint_not_found"], nodes=list(ends.values()))

            paths, pruned = self._bounded_paths(
                session,
                from_id,
                to_id,
                max_depth=max_depth,
                max_paths=max_paths,
            )

            all_ids = {from_id, to_id}
            for path in paths:
                all_ids.update(path.node_ids)
            nodes = list(_fetch_by_ids(session, list(all_ids)).values())

            warnings: list[str] = []
            if not paths:
                warnings.append("no_path")

            return ToolResult(
                nodes=nodes,
                paths=paths,
                pruning=PruningStats(
                    considered=len(paths) + pruned,
                    returned=len(paths),
                    pruned=pruned,
                ),
                meta={
                    "from_id": from_id,
                    "to_id": to_id,
                    "max_depth": max_depth,
                    "max_branching": MAX_BRANCHING,
                    "max_frontier_paths": MAX_FRONTIER_PATHS,
                },
                warnings=warnings,
                evidence=[f"onet:paths:{from_id}->{to_id}"],
            )

    @staticmethod
    def _bounded_paths(
        session: Session,
        from_id: str,
        to_id: str,
        *,
        max_depth: int,
        max_paths: int,
    ) -> tuple[list[Path], int]:
        frontier: list[Path] = [Path(node_ids=[from_id])]
        found: list[Path] = []
        pruned = 0

        for _depth in range(max_depth):
            if not frontier or len(found) >= max_paths:
                break
            frontier_ids = sorted({path.node_ids[-1] for path in frontier})
            result = session.run(
                f"""
                UNWIND $frontier_ids AS current_id
                MATCH (current:{LABEL_ONET_NODE} {{id: current_id}})
                      -[r]-(neighbor:{LABEL_ONET_NODE})
                WHERE current.source = $source AND neighbor.source = $source
                  AND type(r) IN $types AND neighbor.id IS NOT NULL
                RETURN current_id, neighbor.id AS neighbor_id,
                       type(r) AS rel_type, properties(r) AS rel_props,
                       startNode(r).id AS from_id, endNode(r).id AS to_id
                ORDER BY current_id,
                         CASE coalesce(r.relation_type, '')
                           WHEN 'essential' THEN 0
                           WHEN 'transferable' THEN 1
                           ELSE 2
                         END,
                         type(r), neighbor.id
                """,
                frontier_ids=frontier_ids,
                source=SOURCE,
                types=sorted(TRAVERSABLE_RELS),
            )
            by_node: dict[str, list[dict[str, Any]]] = {}
            for record in result:
                row = dict(record)
                by_node.setdefault(row["current_id"], []).append(row)

            expansions: dict[str, list[dict[str, Any]]] = {}
            for node_id, rows in by_node.items():
                expansions[node_id] = rows[:MAX_BRANCHING]

            next_frontier: list[Path] = []
            for path in frontier:
                rows = by_node.get(path.node_ids[-1], [])
                pruned += max(0, len(rows) - MAX_BRANCHING)
                for row in expansions.get(path.node_ids[-1], []):
                    neighbor_id = row["neighbor_id"]
                    if neighbor_id in path.node_ids:
                        pruned += 1
                        continue
                    edge = Edge(
                        type=row["rel_type"],
                        from_id=row["from_id"],
                        to_id=row["to_id"],
                        properties=dict(row.get("rel_props") or {}),
                    )
                    candidate = Path(
                        node_ids=[*path.node_ids, neighbor_id],
                        edges=[*path.edges, edge],
                    )
                    if neighbor_id == to_id:
                        if len(found) < max_paths:
                            found.append(candidate)
                        else:
                            pruned += 1
                    else:
                        next_frontier.append(candidate)

            next_frontier.sort(key=lambda path: tuple(path.node_ids))
            if len(next_frontier) > MAX_FRONTIER_PATHS:
                pruned += len(next_frontier) - MAX_FRONTIER_PATHS
                next_frontier = next_frontier[:MAX_FRONTIER_PATHS]
            frontier = next_frontier

        pruned += len(frontier)
        return found, pruned

    def score_paths(self, paths: list[Path], policy: PolicyRef) -> ToolResult:
        if policy.name != IMPORTANCE_POLICY.name or policy.version != IMPORTANCE_POLICY.version:
            return ToolResult(
                paths=paths,
                warnings=[f"unknown_policy:{policy.name}"],
                meta={"policy": policy.model_dump()},
            )

        scored: list[ScoredPath] = []
        for path in paths:
            values = _importance_values(path)
            score = sum(values) / len(values) if values else 0.0
            scored.append(ScoredPath(path=path, score=score, policy=policy))
        scored.sort(key=lambda item: (-item.score, tuple(item.path.node_ids)))
        return ToolResult(
            paths=paths,
            scored_paths=scored,
            meta={
                "policy": policy.model_dump(),
                "note": (
                    "onet-importance-v1 is a declared Talent Angels policy: "
                    "mean of HAS_SKILL.importance on edges that carry it. "
                    "It is not an O*NET-published path score."
                ),
            },
            evidence=[f"onet:score:{policy.name}:{policy.version}"],
        )


__all__ = ["IMPORTANCE_POLICY", "OnetSuite"]
