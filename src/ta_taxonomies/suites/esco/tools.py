"""ESCO suite tools: query the loaded graph via the shared suite contract.

Use case: after load.py has populated Neo4j, callers (tests, CLIs, later
TA-agents) use ``EscoSuite`` for Locate/Connect/Pathfind-style operations —
``search_nodes``, ``get_neighbors``, ``enumerate_paths``. Returns contract
``ToolResult`` models, not raw Neo4j records.

Why it exists: keep Cypher and confidence policy in a library so agents do
not reimplement graph access. LangGraph ``@tool`` wiring stays in TA-agents.
``score_paths`` is a deliberate stub until a named TA scoring policy exists
(ESCO occ–skill links are essential/optional only, not numeric weights).
"""

from __future__ import annotations

import functools
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
    ToolResult,
)
from ta_taxonomies.contract.schema import SuiteSchema
from ta_taxonomies.suites._groups import attach_groups, top_groups
from ta_taxonomies.suites._keywords import keyword_query
from ta_taxonomies.suites._wordstart import word_start_pattern
from ta_taxonomies.suites.esco.config import (
    CONF_CASEFOLD_AMBIGUOUS,
    CONF_CASEFOLD_UNIQUE,
    CONF_CONTAINS,
    CONF_EXACT_ALT,
    CONF_EXACT_PREF,
    CONF_HYBRID,
    FULLTEXT_INDEX,
    KIND_ALIASES,
    LABEL_ESCO_NODE,
    LABEL_ISCO_GROUP,
    LABEL_OCCUPATION,
    LABEL_SKILL,
    LABEL_SKILL_GROUP,
    MAX_BRANCHING,
    MAX_FRONTIER_PATHS,
    MAX_PATH_DEPTH,
    MAX_PATHS,
    MIN_WILDCARD_TERM,
    REL_BROADER_THAN,
    REL_CLASSIFIED_UNDER,
    REL_HAS_SKILL,
    SEARCH_LIMIT,
    SEARCH_SCAN_CAP,
    SOURCE,
    TRAVERSABLE_RELS,
)

# Labels search_nodes may interpolate into Cypher. Interpolation is required
# because Cypher cannot parameterise a label, and matching the concrete label
# is the whole point — so the set is closed here rather than trusted.
_SEARCHABLE_LABELS: frozenset[str] = frozenset(
    {LABEL_OCCUPATION, LABEL_SKILL, LABEL_ISCO_GROUP, LABEL_SKILL_GROUP}
)

# Everything Locate returns about a node, as a Cypher map, so a branch can
# collect its full result set and hand back both the count and the top slice.
_NODE_MAP = """{
                id: n.id, pref_label: n.pref_label, source: n.source,
                source_id: n.source_id, uri: n.uri, kind: n.kind,
                code: n.code, description: n.description,
                alt_labels: n.alt_labels, labels: labels(n)
            }"""

# Retrieval heads: the full-text index, or the label scan it replaced. The
# predicates below are identical in both, which is what makes the fallback
# safe — it is slower, never different.
_FULLTEXT_HEAD = "CALL db.index.fulltext.queryNodes($index, $lucene) YIELD node AS n"
_SCAN_HEAD = f"MATCH (n:{LABEL_ESCO_NODE})"

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
  AND ($group_prefix IS NULL OR coalesce(n.isco_group, '') STARTS WITH $group_prefix)
  AND (
    n.pref_label =~ $word_start
    OR any(a IN coalesce(n.alt_labels, []) WHERE a =~ $word_start)
  )
// Titles that contain the query rank ahead of alias-only matches *before* the
// cut, or short unrelated titles with a matching alias crowd them out
// ("engineer" kept "chemist" and lost most "... engineer" titles).
WITH n, CASE WHEN n.pref_label =~ $word_start THEN 0 ELSE 1 END AS alias_only
ORDER BY alias_only, size(n.pref_label), n.id
LIMIT $scan_cap
// Group codes of every title match, not just the top slice: a broad query
// reports how its matches split into occupation groups. Alias-only matches are
// left out ("nursery nurse" would put child care under "nurse").
WITH collect({_NODE_MAP}) AS rows,
     collect(CASE WHEN alias_only = 0 THEN n.isco_group END) AS group_codes
RETURN size(rows) AS total, rows[0..$limit] AS top, group_codes
"""

_FULLTEXT_ALIAS_OR_CASEFOLD = _FULLTEXT_HEAD + _ALIAS_OR_CASEFOLD_BODY
# Tier-5 keyword list. The Lucene score only orders this list for the fusion;
# it never becomes a confidence.
_FULLTEXT_KEYWORDS = f"""
CALL db.index.fulltext.queryNodes($index, $lucene) YIELD node AS n, score
WHERE n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
WITH n, score
ORDER BY score DESC, size(n.pref_label), n.id
LIMIT $limit
RETURN collect({_NODE_MAP}) AS top
"""
_SCAN_ALIAS_OR_CASEFOLD = _SCAN_HEAD + _ALIAS_OR_CASEFOLD_BODY
_FULLTEXT_CONTAINS = _FULLTEXT_HEAD + _CONTAINS_BODY
_SCAN_CONTAINS = _SCAN_HEAD + _CONTAINS_BODY

# Split on anything that is not a letter or digit. Two things follow: the terms
# line up with what the analyzer indexes, and none of them can contain a Lucene
# metacharacter (+ - && ! ( ) [ ] ^ " ~ * ? : \\ /), so wildcard terms built
# from them never need escaping.
_TERM_SPLIT = re.compile(r"[\W_]+", re.UNICODE)


def _query_terms(q: str) -> list[str]:
    return [term for term in _TERM_SPLIT.split(q.lower()) if term]


def _lucene_phrase(q: str) -> str | None:
    """Quoted phrase query for the exact-match tiers, or None if unusable.

    None means "nothing here the index can find" — a query of pure punctuation
    ("+++") indexes to no terms, so the caller must scan instead of concluding
    there is no match.
    """
    if not _query_terms(q):
        return None
    # Inside a phrase only the quote and the backslash keep meaning.
    escaped = q.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _lucene_infix(q: str) -> str | None:
    """Infix-wildcard query mirroring CONTAINS, or None if it would not pay.

    Every term is required (``+``) and wrapped in ``*`` because CONTAINS
    matches inside a word, not just at its start. A term below
    ``MIN_WILDCARD_TERM`` expands over most of the term dictionary and costs
    more than the scan, so those queries decline the index instead.
    """
    terms = _query_terms(q)
    if not terms or any(len(term) < MIN_WILDCARD_TERM for term in terms):
        return None
    return " ".join(f"+*{term}*" for term in terms)


def _exact_pref_cypher(labels: list[str]) -> str:
    """One index seek per concrete label, unioned in a single round trip."""
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


_VECTOR_INDEX = "esco_label_embedding"
_VECTOR_K = 25
#: Neo4j's cosine score is (1 + cos) / 2. Without a floor the ANN always returns
#: its K nearest labels, so a nonsense query ("xyzzy") came back as a dozen
#: occupations tagged hybrid_rrf. Measured with all-MiniLM-L6-v2 over the full
#: graphs (2026-09-30): gibberish queries top out at 0.732 (ESCO) / 0.703
#: (O*NET); natural-language job descriptions start at 0.739 / 0.742. Thin on
#: ESCO — a better-embedded label text is the real fix — but it removes noise.
_VECTOR_MIN_SCORE = 0.74


def _embedding_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


@functools.lru_cache(maxsize=1)
def _embedding_model() -> Any:
    """Load the model once per process (it was reloaded on every search)."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")


def _embed_query(text: str) -> list[float] | None:
    """Return a unit-norm embedding for ``text``, or None if unavailable."""
    if not _embedding_available():
        return None
    vec = _embedding_model().encode([text], normalize_embeddings=True)
    return vec[0].tolist()


def _reciprocal_rank_fusion(
    result_lists: list[list[dict[str, Any]]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """Merge ranked lists via RRF. Returns rows sorted by descending RRF score."""
    scores: dict[str, float] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for lst in result_lists:
        for rank, row in enumerate(lst):
            node_id = str(row.get("id") or "")
            if not node_id:
                continue
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank + 1)
            if node_id not in by_id:
                by_id[node_id] = row
    return sorted(by_id.values(), key=lambda r: -scores[str(r.get("id") or "")])


def _locate_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    """Mirror the Cypher ordering (shortest label first, then id)."""
    return len(row.get("pref_label") or ""), str(row.get("id") or "")


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

    ``pruning`` carries the counts; ``truncated`` is the flag an agent can
    branch on without reading them. Before this, a query with 900 matches and
    a query with 25 were indistinguishable in the response.
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
        evidence=[f"esco:search:{evidence_suffix}"],
        meta={"limit": SEARCH_LIMIT, "matches": total},
    )


_ACRONYM_RE = re.compile(r"\b[A-Z]{2,3}\b")


def _alias_needs_second_opinion(q: str, *row_sets: list[dict[str, Any]]) -> bool:
    """An acronym query that no title contains is not trusted on aliases alone.

    ESCO lists "AI engineer" as an alias of "animal artificial insemination
    technician", and "QA tester" only inside an alias of "localiser"; "HR
    manager" is a correct alias of "human resources manager". Only the meaning
    search can tell them apart.
    """
    if not _ACRONYM_RE.search(q):
        return False
    needle = q.casefold()
    return not any(
        needle in (row.get("pref_label") or "").casefold() for rows in row_sets for row in rows
    )


def _locate_merged_result(
    alt_rows: list[dict[str, Any]],
    contains_rows: list[dict[str, Any]],
    total: int,
    notes: list[str],
    evidence_suffix: str,
    *,
    meaning_rows: list[dict[str, Any]] | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> ToolResult:
    """Build a Locate result where each row keeps the tier that matched it.

    Exact alt_label hits keep ``exact_alt`` 0.90, substring hits stay
    ``contains`` 0.70 and meaning-search hits are ``hybrid_rrf``. Rows are
    deduped by id, the stronger tier winning. This keeps the confidence scale
    honest when the sets are merged: an alias that is exactly the query is
    never demoted to a guess.
    """
    tiers = (
        (alt_rows, CONF_EXACT_ALT, "exact_alt"),
        (contains_rows, CONF_CONTAINS, "contains"),
        (meaning_rows or [], CONF_HYBRID, "hybrid_rrf"),
    )
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for rows, confidence, method in tiers:
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            candidates.append(
                Candidate(node=_record_to_node(row), confidence=confidence, method=method)
            )
    warnings = [*notes, *extra_warnings]
    if len(candidates) > 1:
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
            considered=max(1, len(candidates) + pruned),
            returned=len(candidates),
            pruned=pruned,
        ),
        warnings=warnings,
        evidence=[f"esco:search:{evidence_suffix}"],
        meta={"limit": SEARCH_LIMIT, "matches": total},
    )


def _record_to_node(rec: dict[str, Any]) -> Node:
    labels = list(rec.get("labels") or [])
    kind = rec.get("kind") or (labels[0] if labels else "Node")
    return Node(
        id=rec["id"],
        kind=str(kind),
        label=rec.get("pref_label") or "",
        source="esco",
        source_id=rec.get("source_id") or rec.get("uri") or rec["id"],
        properties={
            k: v
            for k, v in rec.items()
            if k
            not in {
                "id",
                "pref_label",
                "source",
                "source_id",
                "labels",
                "kind",
            }
            and v is not None
        },
    )


def _fetch_by_ids(session: Session, ids: list[str]) -> dict[str, Node]:
    if not ids:
        return {}
    result = session.run(
        f"""
        MATCH (n:{LABEL_ESCO_NODE})
        WHERE n.source = $source AND n.id IN $ids
        RETURN n.id AS id, n.pref_label AS pref_label, n.source AS source,
               n.source_id AS source_id, n.uri AS uri, n.kind AS kind,
               n.code AS code, n.description AS description,
               n.alt_labels AS alt_labels, n.skill_type AS skill_type,
               n.reuse_level AS reuse_level, n.isco_group AS isco_group,
               labels(n) AS labels
        """,
        ids=ids,
        source=SOURCE,
    )
    out: dict[str, Node] = {}
    for rec in result:
        node = _record_to_node(dict(rec))
        out[node.id] = node
    return out


class EscoSuite:
    """ESCO implementation of the suite contract (read path against Neo4j).

    Construct with a neo4j ``Driver`` from ``db.neo4j_driver`` (Docker or Aura).
    Call after the graph has been loaded; does not ingest xlsx/fixture data.
    """

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
            group_node_kinds=frozenset({"ISCOGroup", "isco group"}),
        )

    def search_nodes(self, text: str, kind: str | None = None) -> ToolResult:
        """Locate: resolve free text to ESCO nodes with confidence.

        Order: exact preferred label → exact alt label → case-insensitive
        preferred → substring on pref/alts. Never invents hits (``not_found``).
        ``kind`` optionally restricts labels (see ``KIND_ALIASES`` in config).

        The four tiers and their confidences are unchanged; only how candidates
        are *retrieved* changed. Tier 1 seeks the concrete-label range index;
        tiers 2–4 retrieve through the full-text index and then re-apply the
        original predicate, so the answer set is the same one the old scan
        produced. Results are capped at ``SEARCH_LIMIT`` and the cap is now
        reported (``pruning`` + ``truncated``) instead of applied silently.
        """
        q = (text or "").strip()
        if not q:
            return ToolResult(warnings=["empty_query"])

        labels = [
            LABEL_OCCUPATION,
            LABEL_SKILL,
            LABEL_ISCO_GROUP,
            LABEL_SKILL_GROUP,
        ]
        if kind:
            normalized_kind = kind.lower().replace(" ", "_")
            mapped = KIND_ALIASES.get(normalized_kind)
            if mapped is None:
                return ToolResult(warnings=[f"unknown_kind:{kind}"])
            labels = [mapped]

        notes: list[str] = []
        with self._session() as session:
            # 1) exact preferredLabel (case-sensitive) — concrete-label seek
            total, rows = self._match_exact_pref(session, labels, q)
            if rows:
                return _locate_result(
                    rows, total, CONF_EXACT_PREF, "exact_pref", f"exact_pref:{q}", notes
                )

            # 2+3) exact alt_label, then case-insensitive preferred label.
            # Both need the same candidate pool, so they share one round trip.
            alt_total, alt_rows, cf_total, cf_rows = self._match_exact_alias_or_casefold(
                session, labels, q, notes
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

            # 4) substring on pref_label or alt_labels (case-insensitive).
            # Also merge any exact alt_label hits from Tier 2 so that a query
            # like "nurse" does not exit early on nanny/midwife (which have
            # "nurse" as a historical alt_label) before "registered nurse"
            # (whose pref_label contains the word) is ever considered.
            # group_and_sort_locate in TA-agents re-ranks the merged set and
            # promotes pref_label token matches above alt_label exact matches.
            # Each row keeps the tier that matched it (merging never demotes an
            # exact alias to a guess).
            total, rows, group_codes = self._match_contains(session, labels, q, notes)
            if alt_rows:
                total = len(alt_rows) + sum(
                    1 for row in rows if row["id"] not in {a["id"] for a in alt_rows}
                )
            if (alt_rows or rows) and _alias_needs_second_opinion(q, alt_rows, rows):
                query_vector = _embed_query(q)
                meaning_rows = (
                    self._match_vector(session, labels, query_vector, notes)
                    if query_vector is not None
                    else []
                )
                alias_ids = {row["id"] for row in [*alt_rows, *rows]}
                if meaning_rows and meaning_rows[0]["id"] not in alias_ids:
                    # The meaning search disagrees with the alias: offer both.
                    return _locate_merged_result(
                        alt_rows,
                        rows,
                        len(alias_ids | {row["id"] for row in meaning_rows}),
                        notes,
                        f"alias_unconfirmed:{q}",
                        meaning_rows=meaning_rows,
                        extra_warnings=("alias_unconfirmed",),
                    )
            if alt_rows or rows:
                if alt_rows:
                    merged = _locate_merged_result(
                        alt_rows,
                        rows,
                        total,
                        notes,
                        f"contains:{q}",
                    )
                    return self._with_groups(session, merged, group_codes)
                found = _locate_result(
                    rows,
                    total,
                    CONF_CONTAINS,
                    "contains",
                    f"contains:{q}",
                    notes,
                    ambiguous=total > 1,
                )
                return self._with_groups(session, found, group_codes)

            # 5) Hybrid BM25 + vector (semantic fallback for natural-language
            # queries with no keyword overlap in any label). Only reached when
            # Tiers 1–4 all returned nothing. Gracefully skips vector if the
            # embed.py index has not been built yet.
            embedding = _embed_query(q)
            bm25_rows = self._match_keywords(session, labels, q, notes)
            vector_rows: list[dict[str, Any]] = []
            if embedding is not None:
                vector_rows = self._match_vector(session, labels, embedding, notes)
            hybrid_rows = _reciprocal_rank_fusion([bm25_rows, vector_rows])
            if not hybrid_rows:
                return ToolResult(
                    warnings=["not_found", *notes],
                    evidence=[f"esco:search:not_found:{q}"],
                )
            return _locate_result(
                hybrid_rows[:SEARCH_LIMIT],
                len(hybrid_rows),
                CONF_HYBRID,
                "hybrid_rrf",
                f"hybrid:{q}",
                notes,
                ambiguous=len(hybrid_rows) > 1,
            )

    def search_group(self, text: str, group: str) -> ToolResult:
        """Occupations matching ``text`` inside one ISCO group.

        ``group`` is a code from ``meta.groups`` of an earlier search ("2142");
        a shorter code covers its sub-groups ("214"). Narrows a broad match set
        once the user says which area they mean.
        """
        q = (text or "").strip()
        code = (group or "").strip()
        if not q:
            return ToolResult(warnings=["empty_query"])
        if not code.isdigit():
            return ToolResult(warnings=[f"unknown_group:{group}"])
        notes: list[str] = []
        with self._session() as session:
            total, rows, _ = self._match_contains(
                session, [LABEL_OCCUPATION], q, notes, group_prefix=code
            )
        if not rows:
            return ToolResult(
                warnings=["not_found", *notes],
                evidence=[f"esco:search_group:{code}:not_found:{q}"],
            )
        return _locate_result(
            rows, total, CONF_CONTAINS, "contains", f"group:{code}:{q}", notes, ambiguous=total > 1
        )

    @staticmethod
    def _with_groups(session: Session, result: ToolResult, codes: list[str]) -> ToolResult:
        """Name the ISCO unit groups a contains match set falls into."""
        shown, total = top_groups(codes)
        if total < 2:
            return result
        names = {
            record["code"]: {"id": record["id"], "label": record["label"]}
            for record in session.run(
                f"MATCH (g:{LABEL_ISCO_GROUP}) WHERE g.source = $source AND g.code IN $codes "
                "RETURN g.code AS code, g.id AS id, g.pref_label AS label",
                codes=[code for code, _ in shown],
                source=SOURCE,
            )
        }
        return attach_groups(result, shown, total, names, "isco-08")

    # -- Locate retrieval ---------------------------------------------------
    #
    # Each helper returns (total_matches, capped_rows). The total is what makes
    # truncation reportable; before this it was unknowable, because the query
    # stopped at LIMIT 25 and nobody counted the rest.

    @staticmethod
    def _match_exact_pref(
        session: Session,
        labels: list[str],
        q: str,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Exact preferred label, one range-index seek per concrete label.

        The umbrella ``:EscoNode`` + ``labels(n)`` filter this replaces could
        not reach the per-label ``pref_label`` index and scanned every node.
        Ordering and the cap are applied here rather than in Cypher so the
        query stays a plain UNION, which is stable across Cypher versions.
        """
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
        """Exact alias membership and case-insensitive preferred label at once.

        Retrieval is a Lucene phrase query, which is a superset of both
        predicates: a node whose alias equals ``q`` (or whose preferred label
        equals it apart from case) necessarily contains ``q``'s terms in that
        order. The exact predicates are then re-applied in Cypher, so the
        result is identical to the scan's — just over a few hundred candidates
        instead of the whole graph.
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
            # No indexable terms in the query, or no full-text index on this
            # graph. Fall back to the original scan: slower, same answer.
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
        group_prefix: str | None = None,
    ) -> tuple[int, list[dict[str, Any]], list[str]]:
        """Case-insensitive word-start match on pref_label or any alt label.

        Retrieval uses infix wildcards (``+*data* +*scien*``) rather than
        prefix ones, because CONTAINS is an infix predicate: ``"data scien"``
        matches "metadata science", which ``+data*`` would miss. The CONTAINS
        predicate itself is re-applied in Cypher, so this returns the same
        nodes as the scan did.
        """
        lucene = _lucene_infix(q)
        record = None
        if lucene is not None:
            record = self._run_fulltext(
                session,
                _FULLTEXT_CONTAINS,
                notes,
                lucene=lucene,
                word_start=word_start_pattern(q),
                group_prefix=group_prefix,
                labels=labels,
                source=SOURCE,
                index=FULLTEXT_INDEX,
                limit=SEARCH_LIMIT,
                scan_cap=SEARCH_SCAN_CAP,
            )
        if record is None:
            record = session.run(
                _SCAN_CONTAINS,
                word_start=word_start_pattern(q),
                group_prefix=group_prefix,
                labels=labels,
                source=SOURCE,
                limit=SEARCH_LIMIT,
                scan_cap=SEARCH_SCAN_CAP,
            ).single()
        if record is None:
            return 0, [], []
        codes = [str(code) for code in (record.get("group_codes") or []) if code]
        return int(record["total"]), [dict(row) for row in record["top"]], codes

    def _match_keywords(
        self,
        session: Session,
        labels: list[str],
        q: str,
        notes: list[str],
    ) -> list[dict[str, Any]]:
        """Labels sharing topic words with ``q``, most shared words first."""
        lucene = keyword_query(q)
        if lucene is None:
            return []
        record = self._run_fulltext(
            session,
            _FULLTEXT_KEYWORDS,
            notes,
            lucene=lucene,
            labels=labels,
            source=SOURCE,
            index=FULLTEXT_INDEX,
            limit=SEARCH_LIMIT,
        )
        if record is None:
            return []
        return [dict(row) for row in record["top"]]

    def _match_vector(
        self,
        session: Session,
        labels: list[str],
        embedding: list[float],
        notes: list[str],
    ) -> list[dict[str, Any]]:
        """Vector ANN search — returns top-K nodes by cosine similarity.

        Falls back silently to an empty list when the vector index does not
        exist (e.g. embed.py has not been run yet). The note
        ``vector_index_missing`` is added once so callers know why it is slow.
        """
        try:
            result = session.run(
                f"""
                CALL db.index.vector.queryNodes($index, $k, $embedding)
                YIELD node AS n, score
                WHERE score >= $min_score
                  AND n.source = $source
                  AND any(x IN labels(n) WHERE x IN $labels)
                RETURN {_NODE_MAP} AS node
                LIMIT $limit
                """,
                index=_VECTOR_INDEX,
                k=_VECTOR_K,
                min_score=_VECTOR_MIN_SCORE,
                embedding=embedding,
                source=SOURCE,
                labels=labels,
                limit=SEARCH_LIMIT,
            )
            return [dict(record["node"]) for record in result]
        except Exception as exc:
            msg = str(exc).lower()
            if "no such index" in msg or "vector index" in msg or "no index" in msg:
                if "vector_index_missing" not in notes:
                    notes.append("vector_index_missing")
                return []
            raise

    @staticmethod
    def _run_fulltext(
        session: Session,
        cypher: str,
        notes: list[str],
        **params: Any,
    ) -> Any:
        """Run a full-text query, returning None if the index is not there.

        A graph loaded before this index existed must keep working rather than
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
                """
                MATCH (a:EscoNode {id: $id})-[r]->(b:EscoNode)
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN a.id AS from_id, b.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props,
                       b.pref_label AS pref_label, b.source AS source,
                       b.source_id AS source_id, b.uri AS uri, b.kind AS kind,
                       b.code AS code, b.description AS description,
                       b.alt_labels AS alt_labels, labels(b) AS labels
                UNION
                MATCH (b:EscoNode)-[r]->(a:EscoNode {id: $id})
                WHERE a.source = $source AND b.source = $source AND type(r) IN $types
                RETURN b.id AS from_id, a.id AS to_id, type(r) AS rel_type,
                       properties(r) AS rel_props,
                       b.pref_label AS pref_label, b.source AS source,
                       b.source_id AS source_id, b.uri AS uri, b.kind AS kind,
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
                # check node exists
                exists = session.run(
                    "MATCH (n:EscoNode {id: $id}) WHERE n.source = $source RETURN n.id AS id",
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
                nodes_map[neighbor_id] = _record_to_node(
                    {
                        "id": neighbor_id,
                        "pref_label": r.get("pref_label"),
                        "source": r.get("source"),
                        "source_id": r.get("source_id"),
                        "uri": r.get("uri"),
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
                evidence=[f"esco:neighbors:{node_id}"],
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
                evidence=[f"esco:paths:{from_id}->{to_id}"],
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
            result = session.run(
                f"""
                UNWIND $frontier_ids AS current_id
                MATCH (current:{LABEL_ESCO_NODE} {{id: current_id}})
                      -[r]-(neighbor:{LABEL_ESCO_NODE})
                WHERE current.source = $source AND neighbor.source = $source
                  AND type(r) IN $types AND neighbor.id IS NOT NULL
                RETURN current_id, neighbor.id AS neighbor_id,
                       type(r) AS rel_type, properties(r) AS rel_props,
                       startNode(r).id AS from_id, endNode(r).id AS to_id
                ORDER BY current_id,
                         CASE coalesce(r.relation_type, '')
                           WHEN 'essential' THEN 0 WHEN 'optional' THEN 1 ELSE 2
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

        # Branches still present were cut by max_depth or because max_paths was reached.
        pruned += len(frontier)
        return found, pruned

    def score_paths(self, paths: list[Path], policy: PolicyRef) -> ToolResult:
        return ToolResult(
            paths=paths,
            warnings=[
                "score_paths_not_implemented",
                "ESCO edges are binary (essential/optional); scoring is a declared "
                "policy decision, not source data. "
                f"Requested policy={policy.name!r} version={policy.version!r}.",
            ],
            meta={"policy": policy.model_dump()},
        )


# re-export rel constants for tests
__all__ = [
    "EscoSuite",
    "REL_BROADER_THAN",
    "REL_CLASSIFIED_UNDER",
    "REL_HAS_SKILL",
]
