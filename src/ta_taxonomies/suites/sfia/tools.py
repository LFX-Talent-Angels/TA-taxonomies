"""SFIA suite tools: query the loaded graph via the shared suite contract.

Use case: after load.py has populated Neo4j, callers use ``SfiaSuite`` for
Locate / Connect / Pathfind / Evaluate — ``search_nodes``, ``get_neighbors``,
``enumerate_paths``, ``score_paths``. Returns contract ``ToolResult`` models,
not raw Neo4j records.

Three things differ from the ESCO and O\\*NET implementations, all because the
source does:

* **There is no alias tier at all.** ESCO ships curated altLabels and O\\*NET
  ships 57k lay job titles; SFIA publishes exactly one name per skill. So
  Locate goes code → exact name → case-insensitive name → substring, and a
  user's own wording either reaches the substring tier or nothing. The
  four-letter code tier is first because "PROG level 4" is how SFIA is actually
  spoken.
* **``get_neighbors`` warns about a value it refuses to invent.** TA-agents
  filters neighbours on ``relation_type`` and defaults it to ``"essential"``,
  so an ESCO-shaped caller silently drops every edge here. SFIA publishes no
  essential/optional distinction and no occupation-to-skill edge to carry one,
  so this suite says so in the result instead of fabricating one.
* **``score_paths`` ranks by responsibility, not by strength.** SFIA has no edge
  weights; it has an ordinal, the level numbers 1–7. Turning that into a path
  score is our modelling decision, so the policies are named, versioned, and an
  unknown one is refused.
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
from ta_taxonomies.suites.sfia.config import (
    CONF_CASEFOLD_AMBIGUOUS,
    CONF_CASEFOLD_UNIQUE,
    CONF_CONTAINS,
    CONF_EXACT_CODE,
    CONF_EXACT_PREF,
    CONFIDENCE_POLICY,
    FULLTEXT_INDEX,
    KIND_ALIASES,
    LABEL_SFIA_NODE,
    LEVEL_MAX,
    LEVEL_MIN,
    MAX_BRANCHING_PER_REL,
    MAX_FRONTIER_PATHS,
    MAX_PATH_DEPTH,
    MAX_PATHS,
    MIN_WILDCARD_TERM,
    POLICY_LEVEL_BOTTLENECK,
    POLICY_LEVEL_MEAN,
    POLICY_LEVEL_PEAK,
    REL_HAS_LEVEL,
    REL_MAY_LEAD_TO,
    RELATION_TYPE_POLICY,
    SEARCH_LIMIT,
    SEARCH_SCAN_CAP,
    SEARCHABLE_LABELS,
    SKILL_CODE_LENGTH,
    SOURCE,
    SUPPORTED_POLICIES,
    TRAVERSABLE_RELS,
    UNWEIGHTED_EDGE_SCORE,
    VERSION,
)

# Labels search_nodes may interpolate into Cypher. Cypher cannot parameterise a
# label and matching the concrete one is the whole point, so the set is closed
# rather than trusted from the caller.
#
# Checked against SEARCHABLE_LABELS itself rather than a frozenset copy taken at
# import: a copy is a second source of truth that can drift from the config it
# was built from, and the drift would only show as a documented `kind` raising
# at query time.


def _is_searchable(label: str) -> bool:
    return label in SEARCHABLE_LABELS


# No `alt_labels` here, and that is not an omission: SFIA publishes one name per
# skill. A node carrying an always-empty alias list would invite a Locate tier
# that can never match.
_NODE_MAP = """{
                id: n.id, pref_label: n.pref_label, source: n.source,
                source_id: n.source_id, kind: n.kind, code: n.code,
                level: n.level, levels: n.levels, level_count: n.level_count,
                min_level: n.min_level, max_level: n.max_level,
                category: n.category, page_slug: n.page_slug,
                source_url: n.source_url,
                version: n.version, labels: labels(n)
            }"""

_FULLTEXT_HEAD = "CALL db.index.fulltext.queryNodes($index, $lucene) YIELD node AS n"
_SCAN_HEAD = f"MATCH (n:{LABEL_SFIA_NODE})"

_CASEFOLD_BODY = f"""
WHERE n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
  AND toLower(n.pref_label) = toLower($q)
WITH n ORDER BY size(n.pref_label), n.id
LIMIT $scan_cap
WITH collect({_NODE_MAP}) AS rows
RETURN size(rows) AS total, rows[0..$limit] AS top
"""

_CONTAINS_BODY = f"""
WHERE n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
  AND toLower(n.pref_label) CONTAINS toLower($q)
WITH n ORDER BY size(n.pref_label), n.id
LIMIT $scan_cap
WITH collect({_NODE_MAP}) AS rows
RETURN size(rows) AS total, rows[0..$limit] AS top
"""

_FULLTEXT_CASEFOLD = _FULLTEXT_HEAD + _CASEFOLD_BODY
_SCAN_CASEFOLD = _SCAN_HEAD + _CASEFOLD_BODY
_FULLTEXT_CONTAINS = _FULLTEXT_HEAD + _CONTAINS_BODY
_SCAN_CONTAINS = _SCAN_HEAD + _CONTAINS_BODY

# Split on anything that is not a letter or digit, so the terms line up with
# what the analyzer indexed and none of them can contain a Lucene
# metacharacter — wildcard terms built from them never need escaping.
_TERM_SPLIT = re.compile(r"[\W_]+", re.UNICODE)

_LOOKS_LIKE_CODE = re.compile(rf"^[A-Za-z]{{{SKILL_CODE_LENGTH}}}$")


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
        source="sfia",
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
        if not _is_searchable(label):
            raise ValueError(f"label is not searchable: {label!r}")
        arms.append(
            f"MATCH (n:{label})\n"
            "WHERE n.source = $source AND n.pref_label = $q\n"
            f"RETURN {_NODE_MAP} AS node\n"
            "LIMIT $scan_cap"
        )
    return "\nUNION\n".join(arms)


def _code_cypher(labels: list[str]) -> str:
    """One seek per concrete label on the source code (the four-letter skill code)."""
    arms = []
    for label in labels:
        if not _is_searchable(label):
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
    meta_extra: dict[str, Any] | None = None,
) -> ToolResult:
    """Build a Locate result that says how much of the match set it is showing.

    ``meta["confidence_policy"]`` names the policy the confidence came from.
    Without it a caller merging two suites' candidates sees bare floats on scales
    that were never reconciled and no way to tell — see CONFIDENCE_POLICY in
    config.py.
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
    meta: dict[str, Any] = {
        "limit": SEARCH_LIMIT,
        "matches": total,
        "confidence_policy": CONFIDENCE_POLICY.model_dump(),
        "version": VERSION,
    }
    meta.update(meta_extra or {})
    return ToolResult(
        candidates=candidates,
        nodes=[candidate.node for candidate in candidates],
        pruning=PruningStats(
            considered=len(candidates) + pruned,
            returned=len(candidates),
            pruned=pruned,
        ),
        warnings=warnings,
        evidence=[f"sfia:search:{evidence_suffix}"],
        meta=meta,
    )


def _fetch_by_ids(session: Session, ids: list[str]) -> dict[str, Node]:
    if not ids:
        return {}
    result = session.run(
        f"""
        MATCH (n:{LABEL_SFIA_NODE})
        WHERE n.source = $source AND n.id IN $ids
        RETURN n.id AS id, n.pref_label AS pref_label, n.source AS source,
               n.source_id AS source_id, n.kind AS kind, n.code AS code,
               n.level AS level, n.levels AS levels, n.level_count AS level_count,
               n.min_level AS min_level, n.max_level AS max_level,
               n.category AS category, n.page_slug AS page_slug,
               n.source_url AS source_url,
               n.version AS version, labels(n) AS labels
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

    Rows arrive already ordered. Returns what survived and how many were cut, so
    the caller can report the second rather than hide it.
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


def _normalize_level(level: float | None) -> float:
    """Map SFIA's 1–7 responsibility scale onto [0, 1]."""
    if level is None:
        return UNWEIGHTED_EDGE_SCORE
    clamped = max(float(LEVEL_MIN), min(float(LEVEL_MAX), float(level)))
    return (clamped - LEVEL_MIN) / (LEVEL_MAX - LEVEL_MIN)


class SfiaSuite:
    """SFIA implementation of the suite contract (read path against Neo4j).

    Construct with a neo4j ``Driver`` from ``db.neo4j_driver``. Call after the
    graph has been loaded; does not ingest source pages.

    What this suite can and cannot answer is worth stating once, because it is
    unlike the other two: **SFIA has no occupations**, so "what skills does a
    data engineer need" has no answer here. What it has and they do not is a
    responsibility axis — seven ordered levels, and the published fact of which
    of them each skill is defined at.
    """

    name = SOURCE

    def __init__(self, driver: Driver, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    def _session(self) -> Session:
        return self._driver.session(database=self._database)

    # -- Locate -------------------------------------------------------------

    def search_nodes(self, text: str, kind: str | None = None) -> ToolResult:
        """Locate: resolve free text to SFIA nodes with confidence.

        Order: exact skill code → exact name → case-insensitive name →
        substring on the name. Never invents hits (``not_found``). ``kind``
        optionally restricts labels (see ``KIND_ALIASES``).

        There is no alias tier, because SFIA publishes no aliases. That is a
        real capability difference rather than a gap to be filled: any synonym
        list this suite matched against would be one we wrote.

        A code hit carries ``meta["code_scope"]``. A four-letter SFIA code is
        not a cross-suite identity — ``ISCO`` here is *Information systems
        coordination*, unrelated to the ILO occupation classification ESCO
        aligns to (ADR-0006 §5) — so the result says which namespace answered.
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
                        f"exact_code:{q.upper()}",
                        notes,
                        ambiguous=total > 1,
                        meta_extra={"code_scope": "sfia-skill-code"},
                    )

            total, rows = self._match_exact_pref(session, labels, q)
            if rows:
                return _locate_result(
                    rows,
                    total,
                    CONF_EXACT_PREF,
                    "exact_pref",
                    f"exact_pref:{q}",
                    notes,
                    ambiguous=total > 1,
                )

            total, rows = self._match_casefold(session, labels, q, notes)
            if total == 1:
                return _locate_result(
                    rows, total, CONF_CASEFOLD_UNIQUE, "casefold_pref", f"casefold:{q}", notes
                )
            if rows:
                return _locate_result(
                    rows,
                    total,
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
                    evidence=[f"sfia:search:not_found:{q}"],
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
        """True for anything shaped like a SFIA skill code (four letters).

        Cheap syntactic screen, not a claim that the code exists — the seek that
        follows decides that, and a four-letter English word that is not a code
        (``data``, ``risk``) simply falls through to the name tiers. Case is
        ignored on the way in because users type ``prog``; the seek upper-cases,
        because the published codes are uppercase.
        """
        return bool(_LOOKS_LIKE_CODE.match(q))

    @staticmethod
    def _match_code(
        session: Session, labels: list[str], q: str
    ) -> tuple[int, list[dict[str, Any]]]:
        result = session.run(
            _code_cypher(labels), q=q.upper(), source=SOURCE, scan_cap=SEARCH_SCAN_CAP
        )
        rows = [dict(record["node"]) for record in result]
        rows.sort(key=_locate_sort_key)
        return len(rows), rows[:SEARCH_LIMIT]

    @staticmethod
    def _match_exact_pref(
        session: Session, labels: list[str], q: str
    ) -> tuple[int, list[dict[str, Any]]]:
        """Exact name, one range-index seek per concrete label."""
        result = session.run(
            _exact_pref_cypher(labels), q=q, source=SOURCE, scan_cap=SEARCH_SCAN_CAP
        )
        rows = [dict(record["node"]) for record in result]
        rows.sort(key=_locate_sort_key)
        return len(rows), rows[:SEARCH_LIMIT]

    def _match_casefold(
        self, session: Session, labels: list[str], q: str, notes: list[str]
    ) -> tuple[int, list[dict[str, Any]]]:
        """Case-insensitive name match, retrieved through the full-text index.

        The index only *retrieves*: a Lucene phrase query is a superset of this
        predicate, and the predicate is re-applied in Cypher, so a relevance
        score never becomes a confidence value. Relevance is not comparable
        across queries, and the confidence scale describes *how* a match was
        made rather than how good it looked.
        """
        return self._retrieve(
            session, _FULLTEXT_CASEFOLD, _SCAN_CASEFOLD, _lucene_phrase(q), q, labels, notes
        )

    def _match_contains(
        self, session: Session, labels: list[str], q: str, notes: list[str]
    ) -> tuple[int, list[dict[str, Any]]]:
        """Case-insensitive substring on the name."""
        return self._retrieve(
            session, _FULLTEXT_CONTAINS, _SCAN_CONTAINS, _lucene_infix(q), q, labels, notes
        )

    def _retrieve(
        self,
        session: Session,
        fulltext_cypher: str,
        scan_cypher: str,
        lucene: str | None,
        q: str,
        labels: list[str],
        notes: list[str],
    ) -> tuple[int, list[dict[str, Any]]]:
        record = None
        if lucene is not None:
            record = self._run_fulltext(
                session,
                fulltext_cypher,
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
                scan_cypher,
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

        Results carry a ``relation_type_absent`` warning and name
        ``RELATION_TYPE_POLICY`` in ``meta``. That is the important part of this
        method. TA-agents keeps only neighbours whose
        ``properties["relation_type"]`` equals the requested kind, and its phrase
        router defaults that kind to ``"essential"``, so an unmodified caller
        drops every edge here and reports nothing — silently, because an empty
        list is a valid answer.

        The O\\*NET suite solves that by projecting its published Importance onto
        the binary. This suite must not: SFIA publishes no occupation-to-skill
        edges and its levels measure responsibility, not how essential a skill
        is to a job. A level-7 edge does not mean "essential"; it means the skill
        is defined for someone setting strategy. So the absence is made visible
        rather than papered over with a number SFIA never published.
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
                MATCH (a:{LABEL_SFIA_NODE} {{id: $id}})-[r]->(b:{LABEL_SFIA_NODE})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN a.id AS from_id, b.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props, b.id AS neighbor_id
                UNION
                MATCH (b:{LABEL_SFIA_NODE})-[r]->(a:{LABEL_SFIA_NODE} {{id: $id}})
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
                    f"MATCH (n:{LABEL_SFIA_NODE} {{id: $id}}) "
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

            return ToolResult(
                nodes=list(nodes_map.values()),
                edges=edges,
                warnings=["relation_type_absent"],
                evidence=[f"sfia:neighbors:{node_id}"],
                meta={
                    "version": VERSION,
                    "relation_type_policy": RELATION_TYPE_POLICY.model_dump(),
                },
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
                    "version": VERSION,
                },
                warnings=[] if paths else ["no_path"],
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
        """Breadth-first expansion with deterministic per-type and frontier caps.

        Two things here were learned from the O\\*NET suite and apply for the
        same structural reason — a graph of extreme hubs. Seven level nodes
        carry every ``HAS_LEVEL`` edge in the graph, so level 4 alone links to
        most of the framework.

        **The cap is per relationship type.** Under one shared cap the ordering
        would decide which whole relationship types survive rather than which
        edges within a type do.

        **A hop that lands on the target is exempt from the cap.** A level's
        fifteen kept neighbours are fifteen of its ~140 skills, almost never the
        one being asked about, so without this the cap hides the answer instead
        of bounding the search — reporting a large, healthy-looking `pruned`
        count while returning nothing.

        What is *not* like O\\*NET: the ordering carries no meaning. O\\*NET can
        keep its strongest edges because Importance is a published strength.
        SFIA publishes no edge strength, so the tie-break is by level where
        there is one and by node id otherwise — deterministic, and no more than
        that. The arrival exemption is what makes Pathfind work here, not the
        ordering.
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
                MATCH (current:{LABEL_SFIA_NODE} {{id: current_id}})
                      -[r]-(neighbor:{LABEL_SFIA_NODE})
                WHERE current.source = $source AND neighbor.source = $source
                  AND type(r) IN $types AND neighbor.id IS NOT NULL
                RETURN current_id, neighbor.id AS neighbor_id,
                       type(r) AS rel_type, properties(r) AS rel_props,
                       startNode(r).id AS from_id, endNode(r).id AS to_id
                ORDER BY current_id,
                         coalesce(r.level, r.to_level, 0) DESC,
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
        """Rank paths by the responsibility they run through.

        Read the score as "how senior is this route", never as "how strong is
        this match". SFIA publishes no edge weights, so there is no strength to
        report; what it publishes is an ordinal — the level numbers 1–7 — and
        turning that into one number per path is a modelling decision of ours.
        Three policies ship:

        ``sfia-level-bottleneck/1``
            The minimum normalised level along the path. A route is as senior as
            its least senior rung — the conservative reading.
        ``sfia-level-peak/1``
            The maximum. Answers "how far up does this route reach".
        ``sfia-level-mean/1``
            The arithmetic mean, for a route judged on its overall altitude
            rather than either extreme.

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
        unweighted_hops = 0
        for path in paths:
            contributions: list[float] = []
            for index, edge in enumerate(path.edges):
                # The node this hop arrives at, so a ladder step walked
                # downward is scored by the rung it lands on rather than the one
                # it left. Path.node_ids is validated to be one longer than
                # edges, so index + 1 is always in range.
                value = self._edge_score(edge, path.node_ids[index + 1])
                if value is None:
                    unweighted_hops += 1
                    value = UNWEIGHTED_EDGE_SCORE
                contributions.append(value)

            if not contributions:
                # A zero-edge path (a single node) has nothing to weigh; the
                # declared neutral value says so, where 0.0 would read as
                # "measured and found worthless".
                score = UNWEIGHTED_EDGE_SCORE
            elif policy.name == POLICY_LEVEL_MEAN.name:
                score = sum(contributions) / len(contributions)
            elif policy.name == POLICY_LEVEL_PEAK.name:
                score = max(contributions)
            elif policy.name == POLICY_LEVEL_BOTTLENECK.name:
                score = min(contributions)
            else:
                # Unreachable while SUPPORTED_POLICIES and this branch agree.
                # Spelled out rather than left as a bare `else` so that adding a
                # policy to the config without a rule here fails loudly instead
                # of silently inheriting whichever rule the fallback happened to
                # be — a named policy returning another policy's number is the
                # exact failure PolicyRef exists to prevent.
                raise ValueError(f"no scoring rule for policy {policy.name!r}")
            scored.append(ScoredPath(path=path, score=score, policy=known))

        scored.sort(key=lambda item: (-item.score, tuple(item.path.node_ids)))

        warnings: list[str] = []
        if unweighted_hops:
            # On a 1–7 scale normalised to [0, 1] the neutral 0.5 *is* level 4,
            # so a structural hop scores like a level-4 one. Reporting the count
            # is what keeps that a declared consequence rather than a hidden one.
            warnings.append(f"unweighted_hops:{unweighted_hops}")
        return ToolResult(
            paths=[item.path for item in scored],
            scored_paths=scored,
            warnings=warnings,
            evidence=[f"sfia:score:{policy.name}/{policy.version}"],
            meta={
                "policy": known.model_dump(),
                "unweighted_edge_score": UNWEIGHTED_EDGE_SCORE,
                "level_scale": [LEVEL_MIN, LEVEL_MAX],
                "version": VERSION,
            },
        )

    @staticmethod
    def _edge_score(edge: Edge, arriving_at: str | None = None) -> float | None:
        """Normalised contribution of one edge, or None when it carries no level.

        ``HAS_LEVEL`` carries the level the skill is defined at, and is
        symmetric: the same rung whichever end you came from.

        ``MAY_LEAD_TO`` is not. It is stored in one direction (level *n* to
        *n+1*) but path expansion is undirected, as in the other suites, so the
        ladder can be walked downward — and a route that steps **down** to
        level 5 must not be scored as if it had reached level 6. Hence
        ``arriving_at``: the hop is worth the rung it actually arrives at.
        Without it, `USUP → Level 6 → Level 5 → ITSP` scored its descent as an
        ascent, which is wrong in the one place the ladder was added to be right.

        ``BROADER_THAN`` and ``RELATED_TO`` carry no level at all and are the
        caller's declared-neutral case.
        """
        props = edge.properties
        if edge.type == REL_HAS_LEVEL:
            level = props.get("level")
            return None if level is None else _normalize_level(float(level))
        if edge.type == REL_MAY_LEAD_TO:
            descending = arriving_at is not None and arriving_at == edge.from_id
            level = props.get("from_level") if descending else props.get("to_level")
            return None if level is None else _normalize_level(float(level))
        return None


__all__ = ["SfiaSuite"]
