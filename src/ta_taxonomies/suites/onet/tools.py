"""O*NET suite tools: query the loaded graph via the shared suite contract.

Use case: after load.py has populated Neo4j, callers use ``OnetSuite`` for
Locate / Connect / Pathfind / Evaluate — ``search_nodes``, ``get_neighbors``,
``enumerate_paths``, ``score_paths``. Returns contract ``ToolResult`` models,
not raw Neo4j records.

Two things differ from the ESCO implementation, both because the source does:

* **Locate matches concrete labels and retrieves through a full-text index
  from the first line of code.** O*NET's alias pool is 57,543 lay job titles
  over 1,016 occupations, so the alias tier is where a scan hurts most.
  The index only *retrieves*; each tier re-applies its original predicate in
  Cypher, so a Lucene relevance score never becomes a confidence value.
* **``score_paths`` is implemented, not stubbed.** O*NET publishes Importance
  and Level per occupation-descriptor pair with a sample size, a standard
  error and 95% confidence bounds, so ranking is derivable from source data.
  Turning those numbers into one number per path is still policy, so the
  policies are named, versioned, and an unknown one is refused rather than
  approximated.
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
    CONF_EXACT_CODE,
    CONF_EXACT_PREF,
    CONFIDENCE_POLICY,
    DROP_SUPPRESSED_IN_SCORING,
    FULLTEXT_INDEX,
    IM_MAX,
    IM_MIN,
    KIND_ALIASES,
    LABEL_ONET_NODE,
    MAX_BRANCHING_PER_REL,
    MAX_FRONTIER_PATHS,
    MAX_PATH_DEPTH,
    MAX_PATHS,
    MIN_WILDCARD_TERM,
    POLICY_BOTTLENECK,
    POLICY_LOWER_CI,
    POLICY_MEAN,
    RELATION_TYPE_POLICY,
    RELEASE,
    SEARCH_LIMIT,
    SEARCH_SCAN_CAP,
    SEARCHABLE_LABELS,
    SOURCE,
    SUPPORTED_POLICIES,
    TRAVERSABLE_RELS,
    UNWEIGHTED_EDGE_SCORE,
)
from ta_taxonomies.suites.onet.ids import OnetIdError, normalize_onetsoc_code

# Labels search_nodes may interpolate into Cypher. Cypher cannot parameterise a
# label and matching the concrete one is the whole point, so the set is closed
# here rather than trusted from the caller.
_SEARCHABLE: frozenset[str] = frozenset(SEARCHABLE_LABELS)

_NODE_MAP = """{
                id: n.id, pref_label: n.pref_label, source: n.source,
                source_id: n.source_id, kind: n.kind, code: n.code,
                description: n.description, alt_labels: n.alt_labels,
                content_model_domain: n.content_model_domain,
                soc_code: n.soc_code, task_type: n.task_type,
                release: n.release, labels: labels(n)
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

# Split on anything that is not a letter or digit, so the terms line up with
# what the analyzer indexed and none of them can contain a Lucene
# metacharacter — wildcard terms built from them never need escaping.
_TERM_SPLIT = re.compile(r"[\W_]+", re.UNICODE)


def _query_terms(q: str) -> list[str]:
    return [term for term in _TERM_SPLIT.split(q.lower()) if term]


def _lucene_phrase(q: str) -> str | None:
    """Quoted phrase query for the exact tiers, or None if unusable.

    None means "nothing here the index can find" — a query of pure punctuation
    indexes to no terms, so the caller must scan rather than conclude there is
    no match.
    """
    if not _query_terms(q):
        return None
    escaped = q.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _lucene_infix(q: str) -> str | None:
    """Infix-wildcard query mirroring CONTAINS, or None if it would not pay.

    Every term is required and wrapped in ``*`` because CONTAINS matches inside
    a word. A term below ``MIN_WILDCARD_TERM`` expands over most of the term
    dictionary and costs more than the scan, so those queries decline the index.
    """
    terms = _query_terms(q)
    if not terms or any(len(term) < MIN_WILDCARD_TERM for term in terms):
        return None
    return " ".join(f"+*{term}*" for term in terms)


def _record_to_node(rec: dict[str, Any]) -> Node:
    labels = list(rec.get("labels") or [])
    kind = rec.get("kind") or (labels[0] if labels else "Node")
    return Node(
        id=rec["id"],
        kind=str(kind),
        label=rec.get("pref_label") or "",
        source="onet",
        source_id=rec.get("source_id") or rec.get("code") or rec["id"],
        properties={
            k: v
            for k, v in rec.items()
            if k not in {"id", "pref_label", "source", "source_id", "labels", "kind"}
            and v is not None
        },
    )


def _locate_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    """Mirror the Cypher ordering (shortest label first, then id)."""
    return len(row.get("pref_label") or ""), str(row.get("id") or "")


def _exact_pref_cypher(labels: list[str]) -> str:
    """One range-index seek per concrete label, unioned in a single round trip."""
    arms = []
    for label in labels:
        if label not in _SEARCHABLE:
            raise ValueError(f"label is not searchable: {label!r}")
        arms.append(
            f"MATCH (n:{label})\n"
            "WHERE n.source = $source AND n.pref_label = $q\n"
            f"RETURN {_NODE_MAP} AS node\n"
            "LIMIT $scan_cap"
        )
    return "\nUNION\n".join(arms)


def _code_cypher(labels: list[str]) -> str:
    """One seek per concrete label on the source code (O*NET-SOC, Element ID)."""
    arms = []
    for label in labels:
        if label not in _SEARCHABLE:
            raise ValueError(f"label is not searchable: {label!r}")
        arms.append(
            f"MATCH (n:{label})\n"
            "WHERE n.source = $source AND n.code = $q\n"
            f"RETURN {_NODE_MAP} AS node\n"
            "LIMIT $scan_cap"
        )
    return "\nUNION\n".join(arms)


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
    """Build a Locate result that says how much of the match set it is showing.

    ``meta["confidence_policy"]`` names the policy the confidence came from.
    Without it a caller merging two suites' candidates sees two bare floats on
    scales that were never reconciled and no way to tell — see the note on
    CONFIDENCE_POLICY in config.py.
    """
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
        # The total itself stopped at the cap; report it as a floor, not a fact.
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
        meta={
            "limit": SEARCH_LIMIT,
            "matches": total,
            "confidence_policy": CONFIDENCE_POLICY.model_dump(),
            "release": RELEASE,
        },
    )


def _fetch_by_ids(session: Session, ids: list[str]) -> dict[str, Node]:
    if not ids:
        return {}
    result = session.run(
        f"""
        MATCH (n:{LABEL_ONET_NODE})
        WHERE n.source = $source AND n.id IN $ids
        RETURN n.id AS id, n.pref_label AS pref_label, n.source AS source,
               n.source_id AS source_id, n.kind AS kind, n.code AS code,
               n.description AS description, n.alt_labels AS alt_labels,
               n.content_model_domain AS content_model_domain,
               n.soc_code AS soc_code, n.task_type AS task_type,
               n.release AS release, labels(n) AS labels
        """,
        ids=ids,
        source=SOURCE,
    )
    out: dict[str, Node] = {}
    for rec in result:
        node = _record_to_node(dict(rec))
        out[node.id] = node
    return out


def _cap_per_rel_type(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep the first ``MAX_BRANCHING_PER_REL`` rows of each relationship type.

    Rows arrive already ordered by Importance within their type. Returns what
    survived and how many were cut, so the caller can report the second rather
    than hide it.
    """
    kept: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    cut = 0
    for row in rows:
        rel_type = row["rel_type"]
        taken = seen.get(rel_type, 0)
        if taken >= MAX_BRANCHING_PER_REL:
            cut += 1
            continue
        seen[rel_type] = taken + 1
        kept.append(row)
    return kept, cut


def _normalize_importance(importance: float | None) -> float:
    """Map the published 1–5 Importance scale onto [0, 1]."""
    if importance is None:
        return UNWEIGHTED_EDGE_SCORE
    clamped = max(IM_MIN, min(IM_MAX, importance))
    return (clamped - IM_MIN) / (IM_MAX - IM_MIN)


class OnetSuite:
    """O*NET implementation of the suite contract (read path against Neo4j).

    Construct with a neo4j ``Driver`` from ``db.neo4j_driver``. Call after the
    graph has been loaded; does not ingest source tables.
    """

    name = SOURCE

    def __init__(self, driver: Driver, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    def _session(self) -> Session:
        return self._driver.session(database=self._database)

    # -- Locate -------------------------------------------------------------

    def search_nodes(self, text: str, kind: str | None = None) -> ToolResult:
        """Locate: resolve free text to O*NET nodes with confidence.

        Order: exact source code → exact preferred title → exact lay title →
        case-insensitive title → substring on title or lay titles. Never
        invents hits (``not_found``). ``kind`` optionally restricts labels
        (see ``KIND_ALIASES``).

        The code tier is first and exists only here: an O*NET-SOC code is an
        identity, so ``"15-1252.00"`` should resolve to one occupation rather
        than fall through to a substring search that also matches whatever
        happens to contain those digits. ESCO has no equivalent tier because
        its identity is a URI nobody types.
        """
        q = (text or "").strip()
        if not q:
            return ToolResult(warnings=["empty_query"])

        labels = list(SEARCHABLE_LABELS)
        if kind:
            normalized_kind = kind.lower().replace(" ", "_")
            mapped = KIND_ALIASES.get(normalized_kind)
            if mapped is None:
                return ToolResult(warnings=[f"unknown_kind:{kind}"])
            labels = [mapped]

        notes: list[str] = []
        with self._session() as session:
            if self._looks_like_code(q):
                total, rows = self._match_code(session, labels, q)
                if rows:
                    return _locate_result(
                        rows,
                        total,
                        CONF_EXACT_CODE,
                        "exact_code",
                        f"exact_code:{q}",
                        notes,
                        ambiguous=total > 1,
                    )

            total, rows = self._match_exact_pref(session, labels, q)
            if rows:
                # An exact title can legitimately hit two nodes: for a SOC with
                # a single O*NET occupation, the SOC group and the occupation
                # carry the same published title. Say so rather than let the
                # caller read a two-element list as one confident answer;
                # kind="occupation" resolves it.
                return _locate_result(
                    rows,
                    total,
                    CONF_EXACT_PREF,
                    "exact_pref",
                    f"exact_pref:{q}",
                    notes,
                    ambiguous=total > 1,
                )

            # Exact lay title and case-insensitive title need the same
            # candidate pool, so they share one round trip.
            alt_total, alt_rows, cf_total, cf_rows = self._match_alias_or_casefold(
                session, labels, q, notes
            )
            if alt_rows:
                return _locate_result(
                    alt_rows,
                    alt_total,
                    CONF_EXACT_ALT,
                    "exact_alt",
                    f"exact_alt:{q}",
                    notes,
                    ambiguous=alt_total > 1,
                )
            if cf_total == 1:
                return _locate_result(
                    cf_rows, cf_total, CONF_CASEFOLD_UNIQUE, "casefold_pref", f"casefold:{q}", notes
                )
            if cf_rows:
                return _locate_result(
                    cf_rows,
                    cf_total,
                    CONF_CASEFOLD_AMBIGUOUS,
                    "casefold_pref_ambiguous",
                    f"casefold:{q}",
                    notes,
                    ambiguous=True,
                )

            total, rows = self._match_contains(session, labels, q, notes)
            if not rows:
                return ToolResult(
                    warnings=["not_found", *notes],
                    evidence=[f"onet:search:not_found:{q}"],
                    meta={"confidence_policy": CONFIDENCE_POLICY.model_dump()},
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
    def _looks_like_code(q: str) -> bool:
        """True for an O*NET-SOC code or a Content Model Element ID.

        Cheap syntactic screen, not a claim that the code exists — the seek
        that follows decides that.
        """
        try:
            normalize_onetsoc_code(q)
            return True
        except OnetIdError:
            pass
        # Element IDs are dotted and start with a digit ("2.B.3.e"); a plain
        # word is not one, and neither is a title containing a full stop.
        return bool(re.fullmatch(r"\d(?:\.[0-9A-Za-z]+)+", q))

    @staticmethod
    def _match_code(
        session: Session, labels: list[str], q: str
    ) -> tuple[int, list[dict[str, Any]]]:
        result = session.run(_code_cypher(labels), q=q, source=SOURCE, scan_cap=SEARCH_SCAN_CAP)
        rows = [dict(record["node"]) for record in result]
        rows.sort(key=_locate_sort_key)
        return len(rows), rows[:SEARCH_LIMIT]

    @staticmethod
    def _match_exact_pref(
        session: Session, labels: list[str], q: str
    ) -> tuple[int, list[dict[str, Any]]]:
        """Exact preferred title, one range-index seek per concrete label."""
        result = session.run(
            _exact_pref_cypher(labels), q=q, source=SOURCE, scan_cap=SEARCH_SCAN_CAP
        )
        rows = [dict(record["node"]) for record in result]
        rows.sort(key=_locate_sort_key)
        return len(rows), rows[:SEARCH_LIMIT]

    def _match_alias_or_casefold(
        self, session: Session, labels: list[str], q: str, notes: list[str]
    ) -> tuple[int, list[dict[str, Any]], int, list[dict[str, Any]]]:
        """Exact lay-title membership and case-insensitive title in one pass.

        Retrieval is a Lucene phrase query, a superset of both predicates: a
        node whose alias equals ``q`` necessarily contains ``q``'s terms in
        that order. The exact predicates are re-applied in Cypher, so the
        answer is the scan's — over a few hundred candidates instead of the
        whole graph.
        """
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
        self, session: Session, labels: list[str], q: str, notes: list[str]
    ) -> tuple[int, list[dict[str, Any]]]:
        """Case-insensitive substring on the title or any lay title."""
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
    def _run_fulltext(session: Session, cypher: str, notes: list[str], **params: Any) -> Any:
        """Run a full-text query, returning None if the index is not there.

        A graph loaded by an older revision must keep answering rather than
        raise at query time; the caller falls back to the scan and the warning
        says why the call was slow.
        """
        try:
            return session.run(cypher, **params).single()
        except ClientError as exc:
            if "no such fulltext" not in str(exc).lower():
                raise
            if "fulltext_index_missing" not in notes:
                notes.append("fulltext_index_missing")
            return None

    # -- Connect ------------------------------------------------------------

    def get_neighbors(self, node_id: str, rel_types: list[str] | None = None) -> ToolResult:
        """Connect: single-hop neighbors over this suite's traversable rels.

        ``meta["relation_type_policy"]`` is present whenever a HAS_SKILL edge
        is returned. Those edges carry a ``relation_type`` of essential /
        optional that O*NET never published — it is projected from Importance
        so that callers written against ESCO's binary keep working. The policy
        name is how a reader tells that value apart from source data.
        """
        allowed = TRAVERSABLE_RELS
        if rel_types:
            requested = set(rel_types)
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
                       properties(r) AS rel_props, b.id AS neighbor_id
                UNION
                MATCH (b:{LABEL_ONET_NODE})-[r]->(a:{LABEL_ONET_NODE} {{id: $id}})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN b.id AS from_id, a.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props, b.id AS neighbor_id
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
                return ToolResult(warnings=["no_neighbors"])

            # One fetch for the centre and every neighbour, so a node's
            # properties are identical however it was reached.
            wanted = {node_id, *(row["neighbor_id"] for row in rows)}
            nodes_map = _fetch_by_ids(session, sorted(wanted))

            edges = [
                Edge(
                    type=row["rel_type"],
                    from_id=row["from_id"],
                    to_id=row["to_id"],
                    properties=dict(row.get("rel_props") or {}),
                )
                for row in rows
            ]

            meta: dict[str, Any] = {"release": RELEASE}
            if any(edge.type == "HAS_SKILL" for edge in edges):
                meta["relation_type_policy"] = RELATION_TYPE_POLICY.model_dump()
            return ToolResult(
                nodes=list(nodes_map.values()),
                edges=edges,
                evidence=[f"onet:neighbors:{node_id}"],
                meta=meta,
            )

    # -- Pathfind -----------------------------------------------------------

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
                session, from_id, to_id, max_depth=max_depth, max_paths=max_paths
            )

            all_ids = {from_id, to_id}
            for path in paths:
                all_ids.update(path.node_ids)
            nodes = list(_fetch_by_ids(session, sorted(all_ids)).values())

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
                    "max_branching_per_rel": MAX_BRANCHING_PER_REL,
                    "max_frontier_paths": MAX_FRONTIER_PATHS,
                    "release": RELEASE,
                },
                warnings=[] if paths else ["no_path"],
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
        """Breadth-first expansion with deterministic per-type and frontier caps.

        Within a relationship type, expansion is ordered by descending
        Importance, so when the cap bites it keeps the strongest edges rather
        than an arbitrary fifteen — possible here and not in ESCO because the
        ordering is a published number, not a tie-break invented to make the
        cut deterministic. Across types the cap is applied separately; see
        MAX_BRANCHING_PER_REL for why one shared cap silently starves every
        relationship O*NET does not rate.
        """
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
                ORDER BY current_id, coalesce(r.importance, 0.0) DESC,
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

            next_frontier: list[Path] = []
            for path in frontier:
                rows = by_node.get(path.node_ids[-1], [])
                # A hop that lands on the target is never speculative, so the
                # branching cap does not apply to it. Without this split the
                # cap hides the answer instead of bounding the search: 120
                # descriptor elements carry ~107k edges, so an element's
                # fifteen strongest neighbours are the fifteen occupations
                # that rate it highest and almost never the one being asked
                # about. Pathfind between two occupations returned nothing at
                # all — with a large, healthy-looking `pruned` count.
                arrivals = [row for row in rows if row["neighbor_id"] == to_id]
                onward = [row for row in rows if row["neighbor_id"] != to_id]
                kept, cut = _cap_per_rel_type(onward)
                pruned += cut
                for row in arrivals + kept:
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
                        node_ids=[*path.node_ids, neighbor_id], edges=[*path.edges, edge]
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

        # Branches still in the frontier were cut by max_depth or by max_paths.
        pruned += len(frontier)
        return found, pruned

    # -- Evaluate -----------------------------------------------------------

    def score_paths(self, paths: list[Path], policy: PolicyRef) -> ToolResult:
        """Rank paths under a named, versioned policy backed by O*NET's ratings.

        Implemented rather than stubbed because the weights are published:
        every HAS_SKILL edge carries Importance and Level with a sample size,
        a standard error and 95% confidence bounds. Three policies ship:

        ``onet-importance-bottleneck/1``
            A path is as strong as its weakest hop — the minimum normalised
            Importance along it. Conservative about long chains.
        ``onet-importance-mean/1``
            Arithmetic mean of normalised Importance. Rewards paths that are
            strong on average even if one hop is weak.
        ``onet-importance-lower-ci/1``
            Like bottleneck, but scoring the **lower 95% confidence bound**
            rather than the point estimate. A rating from eight respondents
            with a wide interval loses to one from thirty with a narrow one,
            without anyone hand-weighting sample size.

        An unrecognised policy is refused. Approximating it would return a
        number under a name that never defined one, which is exactly the thing
        ``PolicyRef`` exists to prevent.
        """
        known = SUPPORTED_POLICIES.get(policy.name)
        if known is None:
            return ToolResult(
                paths=paths,
                warnings=[
                    f"unknown_policy:{policy.name}",
                    f"supported: {sorted(SUPPORTED_POLICIES)}",
                ],
                meta={"policy": policy.model_dump()},
            )
        if known.version != policy.version:
            return ToolResult(
                paths=paths,
                warnings=[
                    f"unknown_policy_version:{policy.name}:{policy.version}",
                    f"supported version: {known.version}",
                ],
                meta={"policy": policy.model_dump()},
            )

        scored: list[ScoredPath] = []
        neutralised_suppressed = 0
        unweighted_hops = 0
        for path in paths:
            contributions: list[float] = []
            for edge in path.edges:
                props = edge.properties
                value: float | None
                if DROP_SUPPRESSED_IN_SCORING and props.get("recommend_suppress"):
                    # O*NET marks these too unreliable to display, so the
                    # number is not evidence. It is replaced by the neutral
                    # value rather than removed: dropping the hop entirely
                    # would shorten the path's evidence and, under a minimum,
                    # *raise* its score — rewarding a route precisely because
                    # its data was untrustworthy.
                    neutralised_suppressed += 1
                    value = UNWEIGHTED_EDGE_SCORE
                else:
                    value = self._edge_score(props, policy.name)
                    if value is None:
                        unweighted_hops += 1
                        value = UNWEIGHTED_EDGE_SCORE
                contributions.append(value)

            if not contributions:
                # A zero-edge path (a single node) has nothing to weigh; the
                # declared neutral value says so, where 0.0 would read as
                # "measured and found worthless".
                score = UNWEIGHTED_EDGE_SCORE
            elif policy.name == POLICY_MEAN.name:
                score = sum(contributions) / len(contributions)
            else:
                score = min(contributions)
            scored.append(ScoredPath(path=path, score=score, policy=known))

        scored.sort(key=lambda item: (-item.score, tuple(item.path.node_ids)))

        warnings: list[str] = []
        if neutralised_suppressed:
            warnings.append(f"neutralised_suppressed_edges:{neutralised_suppressed}")
        if unweighted_hops:
            warnings.append(f"unweighted_hops:{unweighted_hops}")
        return ToolResult(
            paths=[item.path for item in scored],
            scored_paths=scored,
            warnings=warnings,
            evidence=[f"onet:score:{policy.name}/{policy.version}"],
            meta={
                "policy": known.model_dump(),
                "unweighted_edge_score": UNWEIGHTED_EDGE_SCORE,
                "importance_scale": [IM_MIN, IM_MAX],
                "release": RELEASE,
            },
        )

    @staticmethod
    def _edge_score(props: dict[str, Any], policy_name: str) -> float | None:
        """Normalised contribution of one edge, or None when it carries no rating."""
        if policy_name == POLICY_LOWER_CI.name:
            bound = props.get("importance_lower_ci")
            if bound is None:
                return None
            return _normalize_importance(float(bound))
        if policy_name in (POLICY_BOTTLENECK.name, POLICY_MEAN.name):
            importance = props.get("importance")
            if importance is None:
                return None
            return _normalize_importance(float(importance))
        raise ValueError(f"no scoring rule for policy {policy_name!r}")


__all__ = ["OnetSuite"]
