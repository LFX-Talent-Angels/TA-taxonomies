"""SFIA suite tools: query the loaded graph via the shared suite contract.

Use case: after load.py has populated Neo4j, callers (tests, CLIs, later
TA-agents) use ``SfiaSuite`` for Locate/Connect/Pathfind-style operations —
``search_nodes``, ``get_neighbors``, ``enumerate_paths``. Returns contract
``ToolResult`` models, not raw Neo4j records.

Why it exists: keep Cypher and confidence policy in a library so agents do
not reimplement graph access. LangGraph ``@tool`` wiring stays in TA-agents.
Locate runs the shared tiers in ``suites._locate``; SFIA adds a code tier for
skill codes ("PROG") and skill-level codes ("PROG-4").

Licensing: results carry structure and pointers only (``sfia_ref``, ``url``).
SFIA's descriptive text is never in the graph; a caller that needs it fetches
it from the ``url`` at runtime under its own SFIA license. ``score_paths`` is a
deliberate stub until a named TA scoring policy exists (SFIA publishes no
edge weights).
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
    ToolResult,
)
from ta_taxonomies.contract.schema import SuiteSchema
from ta_taxonomies.suites._locate import (
    Confidences,
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
from ta_taxonomies.suites.sfia.config import (
    CONF_CASEFOLD_AMBIGUOUS,
    CONF_CASEFOLD_UNIQUE,
    CONF_CONTAINS,
    CONF_EXACT_ALT,
    CONF_EXACT_PREF,
    CONF_HYBRID,
    FULLTEXT_INDEX,
    KIND_ALIASES,
    LABEL_CATEGORY,
    LABEL_SFIA_NODE,
    LABEL_SKILL,
    LABEL_SKILL_LEVEL,
    LABEL_SUBCATEGORY,
    MAX_BRANCHING,
    MAX_FRONTIER_PATHS,
    MAX_PATH_DEPTH,
    MAX_PATHS,
    MIN_WILDCARD_TERM,
    REL_BROADER_THAN,
    REL_HAS_LEVEL,
    SEARCH_LIMIT,
    SEARCH_SCAN_CAP,
    SOURCE,
    TRAVERSABLE_RELS,
)

# Everything SFIA's Locate needs; the tiers themselves live in suites._locate.
# Skill codes are upper-case letters ("PROG", "ISCO"); a level code adds the
# level ("PROG-4"). Case-sensitive on purpose: "test" is a word to search for,
# "TEST" is the Functional testing skill.
_SKILL_CODE = re.compile(r"[A-Z]{4,6}")
_LEVEL_CODE = re.compile(r"[A-Z]{4,6}-[1-7]")


def _sfia_codes(q: str) -> list[str]:
    return [q] if _SKILL_CODE.fullmatch(q) or _LEVEL_CODE.fullmatch(q) else []


SFIA_LOCATE = LocateConfig(
    source=SOURCE,
    node_label=LABEL_SFIA_NODE,
    # SFIA has no occupations. Only search_group reads this, and with
    # groups=None it answers unknown_group before using it.
    occupation_label=LABEL_SKILL,
    # Levels are left out of the default search: each skill has up to seven,
    # all sharing its name, and they would crowd skills out of the page. Ask
    # with kind="level", by level code ("PROG-4"), or walk HAS_LEVEL.
    default_labels=(LABEL_SKILL, LABEL_SUBCATEGORY, LABEL_CATEGORY),
    kind_aliases=KIND_ALIASES,
    fulltext_index=FULLTEXT_INDEX,
    # Built by `sfia.embed` (levels skipped). Without it, Tier 5 runs on
    # keywords alone with a "vector_index_missing" note.
    vector_index="sfia_label_embedding",
    confidences=Confidences(
        exact_pref=CONF_EXACT_PREF,
        exact_alt=CONF_EXACT_ALT,
        casefold_unique=CONF_CASEFOLD_UNIQUE,
        casefold_ambiguous=CONF_CASEFOLD_AMBIGUOUS,
        contains=CONF_CONTAINS,
        hybrid=CONF_HYBRID,
    ),
    # Pointers, never payloads: the citation and the link to the licensed text.
    extra_node_fields=("sfia_ref", "url", "level", "levels"),
    # Every SFIA node has a source_id; never fall back to the graph id.
    source_id_fallbacks=(),
    groups=None,
    codes_for=_sfia_codes,
    search_limit=SEARCH_LIMIT,
    scan_cap=SEARCH_SCAN_CAP,
    min_wildcard_term=MIN_WILDCARD_TERM,
)


# Names kept for callers and tests that import them from this module.
_SEARCHABLE_LABELS = SFIA_LOCATE.searchable_labels


def _query_terms(q: str) -> list[str]:
    return query_terms(q)


def _lucene_phrase(q: str) -> str | None:
    return lucene_phrase(q)


def _lucene_infix(q: str) -> str | None:
    return lucene_infix(q, SFIA_LOCATE.min_wildcard_term)


def _exact_pref_cypher(labels: list[str]) -> str:
    return queries_for(SFIA_LOCATE).exact_pref(labels)


_SCAN_CONTAINS = queries_for(SFIA_LOCATE).scan_contains
_FULLTEXT_CONTAINS = queries_for(SFIA_LOCATE).fulltext_contains


def _embed_query(text: str) -> list[float] | None:
    """The suite's meaning-search hook; tests replace it to avoid the model."""
    return embed_query(text)


def _record_to_node(rec: dict[str, Any]) -> Node:
    return record_to_node(SFIA_LOCATE, rec)


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
        SFIA_LOCATE, rows, total, confidence, method, evidence_suffix, notes, ambiguous=ambiguous
    )


_NODE_FIELDS = """
       {n}.pref_label AS pref_label, {n}.source AS source,
       {n}.source_id AS source_id, {n}.kind AS kind, {n}.code AS code,
       {n}.sfia_ref AS sfia_ref, {n}.url AS url, {n}.level AS level,
       {n}.levels AS levels, labels({n}) AS labels"""


def _fetch_by_ids(session: Session, ids: list[str]) -> dict[str, Node]:
    if not ids:
        return {}
    result = session.run(
        f"""
        MATCH (n:{LABEL_SFIA_NODE})
        WHERE n.source = $source AND n.id IN $ids
        RETURN n.id AS id, {_NODE_FIELDS.format(n="n")}
        """,
        ids=ids,
        source=SOURCE,
    )
    out: dict[str, Node] = {}
    for rec in result:
        node = _record_to_node(dict(rec))
        out[node.id] = node
    return out


class SfiaSuite:
    """SFIA implementation of the suite contract (read path against Neo4j).

    Construct with a neo4j ``Driver`` from ``db.neo4j_driver`` (Docker or Aura).
    Call after the graph has been loaded; does not ingest fixture/structure data.
    """

    name = SOURCE

    def __init__(self, driver: Driver, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    def _session(self) -> Session:
        return self._driver.session(database=self._database)

    @property
    def suite_schema(self) -> SuiteSchema:
        # SFIA has no occupations, so no occupation->skill relation and no
        # occupation groups. HAS_LEVEL links a skill to its own levels.
        return SuiteSchema(
            skill_rel_types=(),
            optional_rel_values=frozenset(),
            group_rel_type=None,
            group_node_kinds=frozenset(),
        )

    def _locator(self) -> Locator:
        return Locator(SFIA_LOCATE, self._session, lambda q: _embed_query(q))

    def search_nodes(self, text: str, kind: str | None = None) -> ToolResult:
        """Locate: free text or a code to SFIA nodes with confidence.

        A level code ("PROG-4") names a SkillLevel, which the default search
        leaves out, so an unfiltered level-code query searches levels.
        """
        if kind is None and _LEVEL_CODE.fullmatch((text or "").strip()):
            kind = LABEL_SKILL_LEVEL
        return self._locator().search_nodes(text, kind)

    def search_group(self, text: str, group: str) -> ToolResult:
        """SFIA has no occupation groups: always ``unknown_group`` (contract parity)."""
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
                MATCH (a:{LABEL_SFIA_NODE} {{id: $id}})-[r]->(b:{LABEL_SFIA_NODE})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN a.id AS from_id, b.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props, {_NODE_FIELDS.format(n="b")}
                UNION
                MATCH (b:{LABEL_SFIA_NODE})-[r]->(a:{LABEL_SFIA_NODE} {{id: $id}})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN b.id AS from_id, a.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props, {_NODE_FIELDS.format(n="b")}
                """,
                id=node_id,
                types=types,
                source=SOURCE,
            )
            rows = [dict(r) for r in result]
            rows.sort(key=lambda row: (row["rel_type"], row["from_id"], row["to_id"]))
            if not rows:
                # check node exists
                exists = session.run(
                    f"MATCH (n:{LABEL_SFIA_NODE} {{id: $id}}) WHERE n.source = $source "
                    "RETURN n.id AS id",
                    id=node_id,
                    source=SOURCE,
                ).single()
                if not exists:
                    return ToolResult(warnings=["node_not_found"])
                return ToolResult(warnings=["no_neighbors"], nodes=[])

            nodes_map: dict[str, Node] = {}
            edges: list[Edge] = []
            # include center node
            center = _fetch_by_ids(session, [node_id]).get(node_id)
            if center:
                nodes_map[node_id] = center

            for r in rows:
                # neighbor may be from_id or to_id depending on direction
                neighbor_id = r["to_id"] if r["from_id"] == node_id else r["from_id"]
                fields = {
                    k: v
                    for k, v in r.items()
                    if k not in {"from_id", "to_id", "rel_type", "rel_props"}
                }
                nodes_map[neighbor_id] = _record_to_node({"id": neighbor_id, **fields})
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
                evidence=[f"sfia:neighbors:{node_id}"],
            )

    def enumerate_paths(
        self,
        from_id: str,
        to_id: str,
        *,
        max_depth: int = 4,
        max_paths: int = 20,
    ) -> ToolResult:
        """Return bounded, cycle-free routes with explicit pruning counts."""
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
                evidence=[f"sfia:paths:{from_id}->{to_id}"],
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
        """Breadth-first expansion with deterministic per-node and frontier caps."""
        frontier: list[Path] = [Path(node_ids=[from_id])]
        found: list[Path] = []
        pruned = 0

        for _depth in range(max_depth):
            if not frontier or len(found) >= max_paths:
                break
            frontier_ids = sorted({path.node_ids[-1] for path in frontier})
            # SFIA edges carry no properties to rank by; ties use type, then id.
            result = session.run(
                f"""
                UNWIND $frontier_ids AS current_id
                MATCH (current:{LABEL_SFIA_NODE} {{id: current_id}})
                      -[r]-(neighbor:{LABEL_SFIA_NODE})
                WHERE current.source = $source AND neighbor.source = $source
                  AND type(r) IN $types AND neighbor.id IS NOT NULL
                RETURN current_id, neighbor.id AS neighbor_id,
                       type(r) AS rel_type, properties(r) AS rel_props,
                       startNode(r).id AS from_id, endNode(r).id AS to_id
                ORDER BY current_id, type(r), neighbor.id
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

        # Branches still present were cut by max_depth or because max_paths was reached.
        pruned += len(frontier)
        return found, pruned

    def score_paths(self, paths: list[Path], policy: PolicyRef) -> ToolResult:
        return ToolResult(
            paths=paths,
            warnings=[
                "score_paths_not_implemented",
                "SFIA edges are unweighted structure (HAS_LEVEL, BROADER_THAN); scoring "
                "is a declared policy decision, not source data. "
                f"Requested policy={policy.name!r} version={policy.version!r}.",
            ],
            meta={"policy": policy.model_dump()},
        )


# re-export rel constants for tests
__all__ = [
    "SFIA_LOCATE",
    "SfiaSuite",
    "REL_BROADER_THAN",
    "REL_HAS_LEVEL",
]
