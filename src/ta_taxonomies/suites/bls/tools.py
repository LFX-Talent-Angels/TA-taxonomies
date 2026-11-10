"""BLS suite tools: query the loaded graph via the shared suite contract.

Use case: after load.py has populated Neo4j, callers use ``BlsSuite`` for
Locate/Connect/Pathfind-style operations. Returns contract ``ToolResult``
models, not raw Neo4j records.

Locate is the shared engine (``suites._locate``); this module only describes
BLS to it: SOC codes (``15-1252``, and the O*NET form ``15-1252.00``), SOC
major groups for broad searches, and the EP facts each occupation carries.
Ranking routes is the Evaluator's job, so ``score_paths`` is a declared stub.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver, Session

from ta_taxonomies.contract.models import Edge, Node, Path, PolicyRef, PruningStats, ToolResult
from ta_taxonomies.contract.schema import SuiteSchema
from ta_taxonomies.suites._locate import (
    Confidences,
    GroupScheme,
    LocateConfig,
    Locator,
    embed_query,
    record_to_node,
)
from ta_taxonomies.suites.bls.config import (
    CONF_CASEFOLD_AMBIGUOUS,
    CONF_CASEFOLD_UNIQUE,
    CONF_CONTAINS,
    CONF_EXACT_ALT,
    CONF_EXACT_PREF,
    CONF_HYBRID,
    FULLTEXT_INDEX,
    KIND_ALIASES,
    LABEL_BLS_NODE,
    LABEL_EDUCATION,
    LABEL_OCCUPATION,
    LABEL_SKILL,
    LABEL_SOC_GROUP,
    LABEL_TRAINING,
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
    VECTOR_INDEX,
)
from ta_taxonomies.suites.bls.ids import codes_in_query

#: Occupation facts returned with every node, so Locate and Connect can show
#: wage, outlook and education without a second query. Absent on other kinds.
FACT_FIELDS: tuple[str, ...] = (
    "soc_code",
    "median_wage_annual",
    "employment_change_pct",
    "openings_annual",
    "entry_education",
    "work_experience",
    "job_training",
    "projection_aggregate",
    "projection_code",
    "projection_cycle",
    "wage_period",
    "ooh_url",
    "onet_soc_codes",
    "level",
    "rank",
)


def _soc_names(_session: Session, codes: list[str]) -> dict[str, dict[str, Any]]:
    return {
        code: {"id": f"{SOURCE}:soc-group:{code}-0000", "label": SOC_MAJOR_GROUPS.get(code)}
        for code in codes
    }


BLS_LOCATE = LocateConfig(
    source=SOURCE,
    node_label=LABEL_BLS_NODE,
    occupation_label=LABEL_OCCUPATION,
    default_labels=(LABEL_OCCUPATION, LABEL_SKILL, LABEL_SOC_GROUP),
    kind_aliases=KIND_ALIASES,
    fulltext_index=FULLTEXT_INDEX,
    vector_index=VECTOR_INDEX,
    confidences=Confidences(
        exact_pref=CONF_EXACT_PREF,
        exact_alt=CONF_EXACT_ALT,
        casefold_unique=CONF_CASEFOLD_UNIQUE,
        casefold_ambiguous=CONF_CASEFOLD_AMBIGUOUS,
        contains=CONF_CONTAINS,
        hybrid=CONF_HYBRID,
    ),
    extra_node_fields=FACT_FIELDS,
    groups=GroupScheme(
        name="soc-2018-major",
        code_expr="CASE WHEN 'Occupation' IN labels(n) THEN n.soc_major END",
        member_expr="n.code",
        prefix_for=lambda code: f"{code}-" if code in SOC_MAJOR_GROUPS else None,
        names_for=_soc_names,
    ),
    # "15-1252" and the O*NET form "15-1252.00" both name SOC 15-1252.
    codes_for=codes_in_query,
    search_limit=SEARCH_LIMIT,
    scan_cap=SEARCH_SCAN_CAP,
    min_wildcard_term=MIN_WILDCARD_TERM,
)

_NODE_FIELDS = ", ".join(
    [
        "{v}.id AS id",
        "{v}.pref_label AS pref_label",
        "{v}.source AS source",
        "{v}.source_id AS source_id",
        "{v}.kind AS kind",
        "{v}.code AS code",
        "{v}.description AS description",
        "{v}.alt_labels AS alt_labels",
        "labels({v}) AS labels",
        *[f"{{v}}.{field} AS {field}" for field in FACT_FIELDS],
    ]
)


def _fields(var: str) -> str:
    """The RETURN columns of one node, as ``record_to_node`` reads them."""
    return _NODE_FIELDS.replace("{v}", var)


def _embed_query(text: str) -> list[float] | None:
    """The suite's meaning-search hook; tests replace it to avoid the model."""
    return embed_query(text)


def _record_to_node(rec: dict[str, Any]) -> Node:
    return record_to_node(BLS_LOCATE, rec)


def _fetch_by_ids(session: Session, ids: list[str]) -> dict[str, Node]:
    if not ids:
        return {}
    result = session.run(
        f"MATCH (n:{LABEL_BLS_NODE}) WHERE n.source = $source AND n.id IN $ids "
        f"RETURN {_fields('n')}",
        ids=ids,
        source=SOURCE,
    )
    out: dict[str, Node] = {}
    for rec in result:
        node = _record_to_node(dict(rec))
        out[node.id] = node
    return out


class BlsSuite:
    """BLS implementation of the suite contract (read path against Neo4j)."""

    name = SOURCE

    def __init__(self, driver: Driver, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    def _session(self) -> Session:
        return self._driver.session(database=self._database)

    @property
    def suite_schema(self) -> SuiteSchema:
        return SuiteSchema(
            skill_rel_types=("HAS_SKILL",),
            optional_rel_values=frozenset({"optional"}),
            group_rel_type="CLASSIFIED_UNDER",
            group_node_kinds=frozenset({LABEL_SOC_GROUP, "soc group"}),
        )

    def _locator(self) -> Locator:
        return Locator(BLS_LOCATE, self._session, lambda q: _embed_query(q))

    def search_nodes(self, text: str, kind: str | None = None) -> ToolResult:
        """Locate: free text or a SOC code to BLS nodes with confidence."""
        return self._locator().search_nodes(text, kind)

    def search_group(self, text: str, group: str) -> ToolResult:
        """Occupations matching ``text`` inside one SOC major group from ``meta.groups``."""
        return self._locator().search_group(text, group)

    def get_neighbors(self, node_id: str, rel_types: list[str] | None = None) -> ToolResult:
        allowed = TRAVERSABLE_RELS
        if rel_types:
            bad = set(rel_types) - allowed
            if bad:
                return ToolResult(warnings=[f"unknown_rel_types:{sorted(bad)}"])
            types = sorted(set(rel_types))
        else:
            types = sorted(allowed)

        with self._session() as session:
            result = session.run(
                f"""
                MATCH (a:{LABEL_BLS_NODE} {{id: $id}})-[r]->(b:{LABEL_BLS_NODE})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN a.id AS from_id, b.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props, {_fields("b")}
                UNION
                MATCH (b:{LABEL_BLS_NODE})-[r]->(a:{LABEL_BLS_NODE} {{id: $id}})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN b.id AS from_id, a.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props, {_fields("b")}
                """,
                id=node_id,
                types=types,
                source=SOURCE,
            )
            rows = [dict(r) for r in result]
            if not rows:
                exists = session.run(
                    f"MATCH (n:{LABEL_BLS_NODE} {{id: $id}}) "
                    "WHERE n.source = $source RETURN n.id AS id",
                    id=node_id,
                    source=SOURCE,
                ).single()
                if not exists:
                    return ToolResult(warnings=["node_not_found"])
                return ToolResult(warnings=["no_neighbors"], nodes=[])

            # Highest-scored skills first, then by relationship and id.
            rows.sort(
                key=lambda row: (
                    row["rel_type"],
                    -float((row.get("rel_props") or {}).get("score") or 0.0),
                    row["from_id"],
                    row["to_id"],
                )
            )
            nodes_map: dict[str, Node] = {}
            center = _fetch_by_ids(session, [node_id]).get(node_id)
            if center:
                nodes_map[node_id] = center
            edges: list[Edge] = []
            for r in rows:
                nodes_map[r["id"]] = _record_to_node(r)
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
                evidence=[f"bls:neighbors:{node_id}"],
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
                session, from_id, to_id, max_depth=max_depth, max_paths=max_paths
            )
            all_ids = {from_id, to_id}
            for path in paths:
                all_ids.update(path.node_ids)
            nodes = list(_fetch_by_ids(session, list(all_ids)).values())

        return ToolResult(
            nodes=nodes,
            paths=paths,
            pruning=PruningStats(
                considered=len(paths) + pruned, returned=len(paths), pruned=pruned
            ),
            meta={
                "from_id": from_id,
                "to_id": to_id,
                "max_depth": max_depth,
                "max_branching": MAX_BRANCHING,
                "max_frontier_paths": MAX_FRONTIER_PATHS,
            },
            warnings=[] if paths else ["no_path"],
            evidence=[f"bls:paths:{from_id}->{to_id}"],
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
        """Breadth-first, cycle-free, with per-node and frontier caps (as O*NET)."""
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
                MATCH (current:{LABEL_BLS_NODE} {{id: current_id}})
                      -[r]-(neighbor:{LABEL_BLS_NODE})
                WHERE current.source = $source AND neighbor.source = $source
                  AND type(r) IN $types AND neighbor.id IS NOT NULL
                RETURN current_id, neighbor.id AS neighbor_id,
                       type(r) AS rel_type, properties(r) AS rel_props,
                       startNode(r).id AS from_id, endNode(r).id AS to_id
                ORDER BY current_id,
                         CASE coalesce(r.relation_type, '')
                           WHEN 'essential' THEN 0 WHEN 'optional' THEN 1 ELSE 2
                         END,
                         coalesce(r.score, 0) DESC, type(r), neighbor.id
                """,
                frontier_ids=frontier_ids,
                source=SOURCE,
                types=sorted(TRAVERSABLE_RELS),
            )
            by_node: dict[str, list[dict[str, Any]]] = {}
            for record in result:
                row = dict(record)
                by_node.setdefault(row["current_id"], []).append(row)

            next_frontier: list[Path] = []
            for path in frontier:
                rows = by_node.get(path.node_ids[-1], [])
                pruned += max(0, len(rows) - MAX_BRANCHING)
                for row in rows[:MAX_BRANCHING]:
                    neighbor_id = row["neighbor_id"]
                    if neighbor_id in path.node_ids:
                        pruned += 1
                        continue
                    candidate = Path(
                        node_ids=[*path.node_ids, neighbor_id],
                        edges=[
                            *path.edges,
                            Edge(
                                type=row["rel_type"],
                                from_id=row["from_id"],
                                to_id=row["to_id"],
                                properties=dict(row.get("rel_props") or {}),
                            ),
                        ],
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
        return ToolResult(
            paths=paths,
            warnings=[
                "score_paths_not_implemented",
                "BLS publishes no path score; ranking routes is the Evaluator's job, "
                "under a declared policy. "
                f"Requested policy={policy.name!r} version={policy.version!r}.",
            ],
            meta={"policy": policy.model_dump()},
        )


__all__ = ["BLS_LOCATE", "FACT_FIELDS", "BlsSuite", "LABEL_EDUCATION", "LABEL_TRAINING"]
