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
from neo4j.exceptions import ClientError

from ta_taxonomies.contract.models import (
    Candidate,
    Edge,
    Node,
    Path,
    PolicyRef,
    PruningStats,
    ScoredPath,
    ToolResult,
)
from ta_taxonomies.suites.onet.config import (
    CONF_CASEFOLD_AMBIGUOUS,
    CONF_CASEFOLD_UNIQUE,
    CONF_CONTAINS,
    CONF_EXACT_ALT,
    CONF_EXACT_PREF,
    FULLTEXT_INDEX,
    KIND_ALIASES,
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
    SOURCE,
    TRAVERSABLE_RELS,
)

_SEARCHABLE_LABELS: frozenset[str] = frozenset(KIND_ALIASES.values())
_DEFAULT_SEARCH_LABELS = (
    LABEL_OCCUPATION,
    LABEL_SKILL,
    LABEL_TASK,
    LABEL_SOFTWARE,
    LABEL_KNOWLEDGE,
    LABEL_ABILITY,
    LABEL_WORK_ACTIVITY,
    LABEL_INTEREST,
)

IMPORTANCE_POLICY = PolicyRef(name="onet-importance-v1", version="1")

_NODE_MAP = """{
                id: n.id, pref_label: n.pref_label, source: n.source,
                source_id: n.source_id, kind: n.kind,
                code: n.code, description: n.description,
                alt_labels: n.alt_labels, labels: labels(n)
            }"""

_FULLTEXT_HEAD = "CALL db.index.fulltext.queryNodes($index, $lucene) YIELD node AS n"
_SCAN_HEAD = f"MATCH (n:{LABEL_ONET_NODE})"

_ALIAS_OR_CASEFOLD_BODY = f"""
WHERE n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
  AND ($q IN coalesce(n.alt_labels, []) OR toLower(n.pref_label) = toLower($q))
WITH n, ($q IN coalesce(n.alt_labels, [])) AS is_alt
ORDER BY size(n.pref_label), n.id
LIMIT $scan_cap
WITH collect({{node: {_NODE_MAP}, is_alt: is_alt}}) AS rows
WITH [r IN rows WHERE r.is_alt | r.node] AS alt,
     [r IN rows WHERE NOT r.is_alt | r.node] AS cf
RETURN size(alt) AS alt_total, alt[0..$limit] AS alt_top,
       size(cf) AS cf_total, cf[0..$limit] AS cf_top
"""

_CONTAINS_BODY = f"""
WHERE n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
  AND (
    toLower(n.pref_label) CONTAINS toLower($q)
    OR any(a IN coalesce(n.alt_labels, [])
           WHERE toLower(a) CONTAINS toLower($q))
  )
WITH n ORDER BY size(n.pref_label), n.id
LIMIT $scan_cap
WITH collect({_NODE_MAP}) AS rows
RETURN size(rows) AS total, rows[0..$limit] AS top
"""

_FULLTEXT_ALIAS_OR_CASEFOLD = _FULLTEXT_HEAD + _ALIAS_OR_CASEFOLD_BODY
_SCAN_ALIAS_OR_CASEFOLD = _SCAN_HEAD + _ALIAS_OR_CASEFOLD_BODY
_FULLTEXT_CONTAINS = _FULLTEXT_HEAD + _CONTAINS_BODY
_SCAN_CONTAINS = _SCAN_HEAD + _CONTAINS_BODY

_TERM_SPLIT = re.compile(r"[\W_]+", re.UNICODE)


def _query_terms(q: str) -> list[str]:
    return [term for term in _TERM_SPLIT.split(q.lower()) if term]


def _lucene_phrase(q: str) -> str | None:
    if not _query_terms(q):
        return None
    escaped = q.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _lucene_infix(q: str) -> str | None:
    terms = _query_terms(q)
    if not terms or any(len(term) < MIN_WILDCARD_TERM for term in terms):
        return None
    return " ".join(f"+*{term}*" for term in terms)


def _exact_pref_cypher(labels: list[str]) -> str:
    arms = []
    for label in labels:
        if label not in _SEARCHABLE_LABELS:
            raise ValueError(f"label is not searchable: {label!r}")
        arms.append(
            f"MATCH (n:{label})\n"
            "WHERE n.source = $source AND n.pref_label = $q\n"
            f"RETURN {_NODE_MAP} AS node\n"
            "LIMIT $scan_cap"
        )
    return "\nUNION\n".join(arms)


def _locate_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    return len(row.get("pref_label") or ""), str(row.get("id") or "")


def _record_to_node(rec: dict[str, Any]) -> Node:
    labels = list(rec.get("labels") or [])
    kind = rec.get("kind") or (labels[0] if labels else "Node")
    return Node(
        id=rec["id"],
        kind=str(kind),
        label=rec.get("pref_label") or "",
        source="onet",
        source_id=rec.get("source_id") or rec["id"],
        properties={
            k: v
            for k, v in rec.items()
            if k not in {"id", "pref_label", "source", "source_id", "labels", "kind"}
            and v is not None
        },
    )


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
    candidates = [
        Candidate(node=_record_to_node(row), confidence=confidence, method=method) for row in rows
    ]
    warnings = list(notes)
    if ambiguous:
        warnings.append("ambiguous")
    pruned = max(0, total - len(candidates))
    if pruned:
        warnings.append("truncated")
    if total >= SEARCH_SCAN_CAP:
        warnings.append("match_count_capped")
    return ToolResult(
        candidates=candidates,
        nodes=[candidate.node for candidate in candidates],
        pruning=PruningStats(
            considered=len(candidates) + pruned,
            returned=len(candidates),
            pruned=pruned,
        ),
        warnings=warnings,
        evidence=[f"onet:search:{evidence_suffix}"],
        meta={"limit": SEARCH_LIMIT, "matches": total},
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

    def search_nodes(self, text: str, kind: str | None = None) -> ToolResult:
        q = (text or "").strip()
        if not q:
            return ToolResult(warnings=["empty_query"])

        labels = list(_DEFAULT_SEARCH_LABELS)
        if kind:
            normalized_kind = kind.lower().replace(" ", "_")
            mapped = KIND_ALIASES.get(normalized_kind)
            if mapped is None:
                return ToolResult(warnings=[f"unknown_kind:{kind}"])
            labels = [mapped]

        notes: list[str] = []
        with self._session() as session:
            total, rows = self._match_exact_pref(session, labels, q)
            if rows:
                return _locate_result(
                    rows, total, CONF_EXACT_PREF, "exact_pref", f"exact_pref:{q}", notes
                )

            alt_total, alt_rows, cf_total, cf_rows = self._match_exact_alias_or_casefold(
                session, labels, q, notes
            )
            if alt_rows:
                return _locate_result(
                    alt_rows, alt_total, CONF_EXACT_ALT, "exact_alt", f"exact_alt:{q}", notes
                )
            if cf_total == 1:
                return _locate_result(
                    cf_rows,
                    cf_total,
                    CONF_CASEFOLD_UNIQUE,
                    "casefold_pref",
                    f"casefold_pref:{q}",
                    notes,
                )
            if cf_rows:
                return _locate_result(
                    cf_rows,
                    cf_total,
                    CONF_CASEFOLD_AMBIGUOUS,
                    "casefold_pref_ambiguous",
                    f"casefold_pref:{q}",
                    notes,
                    ambiguous=True,
                )

            total, rows = self._match_contains(session, labels, q, notes)
            if not rows:
                return ToolResult(
                    warnings=["not_found", *notes],
                    evidence=[f"onet:search:not_found:{q}"],
                )
            return _locate_result(
                rows,
                total,
                CONF_CONTAINS,
                "contains",
                f"contains:{q}",
                notes,
                ambiguous=total > 1,
            )

    @staticmethod
    def _match_exact_pref(
        session: Session,
        labels: list[str],
        q: str,
    ) -> tuple[int, list[dict[str, Any]]]:
        result = session.run(
            _exact_pref_cypher(labels),
            q=q,
            source=SOURCE,
            scan_cap=SEARCH_SCAN_CAP,
        )
        rows = [dict(record["node"]) for record in result]
        rows.sort(key=_locate_sort_key)
        return len(rows), rows[:SEARCH_LIMIT]

    def _match_exact_alias_or_casefold(
        self,
        session: Session,
        labels: list[str],
        q: str,
        notes: list[str],
    ) -> tuple[int, list[dict[str, Any]], int, list[dict[str, Any]]]:
        phrase = _lucene_phrase(q)
        record = None
        if phrase is not None:
            record = self._run_fulltext(
                session,
                _FULLTEXT_ALIAS_OR_CASEFOLD,
                notes,
                lucene=phrase,
                q=q,
                labels=labels,
                source=SOURCE,
                index=FULLTEXT_INDEX,
                limit=SEARCH_LIMIT,
                scan_cap=SEARCH_SCAN_CAP,
            )
        if record is None:
            record = session.run(
                _SCAN_ALIAS_OR_CASEFOLD,
                q=q,
                labels=labels,
                source=SOURCE,
                limit=SEARCH_LIMIT,
                scan_cap=SEARCH_SCAN_CAP,
            ).single()
        if record is None:
            return 0, [], 0, []
        return (
            int(record["alt_total"]),
            [dict(row) for row in record["alt_top"]],
            int(record["cf_total"]),
            [dict(row) for row in record["cf_top"]],
        )

    def _match_contains(
        self,
        session: Session,
        labels: list[str],
        q: str,
        notes: list[str],
    ) -> tuple[int, list[dict[str, Any]]]:
        lucene = _lucene_infix(q)
        record = None
        if lucene is not None:
            record = self._run_fulltext(
                session,
                _FULLTEXT_CONTAINS,
                notes,
                lucene=lucene,
                q=q,
                labels=labels,
                source=SOURCE,
                index=FULLTEXT_INDEX,
                limit=SEARCH_LIMIT,
                scan_cap=SEARCH_SCAN_CAP,
            )
        if record is None:
            record = session.run(
                _SCAN_CONTAINS,
                q=q,
                labels=labels,
                source=SOURCE,
                limit=SEARCH_LIMIT,
                scan_cap=SEARCH_SCAN_CAP,
            ).single()
        if record is None:
            return 0, []
        return int(record["total"]), [dict(row) for row in record["top"]]

    @staticmethod
    def _run_fulltext(
        session: Session,
        cypher: str,
        notes: list[str],
        **params: Any,
    ) -> Any:
        try:
            return session.run(cypher, **params).single()
        except ClientError as exc:
            if "no such fulltext" not in str(exc).lower():
                raise
            if "fulltext_index_missing" not in notes:
                notes.append("fulltext_index_missing")
            return None

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
