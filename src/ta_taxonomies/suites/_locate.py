"""Locate (``search_nodes``) for every suite: one engine, one set of rules.

ESCO and O*NET each carried a near-identical copy of this, and every search
fix had to be made twice; a new suite copying one of them would have missed
whichever fix landed last. A suite now describes itself in a
:class:`LocateConfig` — labels, indexes, confidences, how its occupations group
— and gets every rule below, unchanged:

0. ``exact_code``  — a code the suite recognises ("15-1252.00", "2512").
1. ``exact_pref``  — the preferred label exactly (one range-index seek per label).
2. ``exact_alt``   — an alias exactly; merged into tier 4, never an early exit.
3. ``casefold_pref`` — the preferred label apart from case.
4. ``contains``    — the query starts a word in a label or alias ("swe" is not
   inside "Answering"). Title matches rank before alias-only ones before the
   cut, and a broad match reports its occupation groups (``meta.groups``).
5. ``hybrid_rrf``  — only when 1-4 found nothing: a keyword list and a vector
   list fused by reciprocal rank.

An acronym that no title contains ("AI engineer", "QA tester") is not trusted
on aliases alone: the meaning search gets a say and both are offered.

Confidence is always the declared policy value of the tier that matched, never
a Lucene or cosine score.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from neo4j import Session
from neo4j.exceptions import ClientError

from ta_taxonomies.contract.models import Candidate, Node, PruningStats, SuiteName, ToolResult
from ta_taxonomies.suites._groups import attach_groups, top_groups
from ta_taxonomies.suites._keywords import enough_words, keyword_query, topic_stems
from ta_taxonomies.suites._wordstart import word_start_pattern


@dataclass(frozen=True)
class Confidences:
    """The declared confidence of each tier (same scale for every suite)."""

    exact_pref: float = 0.95
    exact_alt: float = 0.90
    casefold_unique: float = 0.85
    casefold_ambiguous: float = 0.80
    contains: float = 0.70
    hybrid: float = 0.75


@dataclass(frozen=True)
class GroupScheme:
    """How a suite's occupations group, for "which area?" and narrowing.

    ``code_expr`` and ``member_expr`` are Cypher expressions over ``n``. They
    are part of the suite's own code, never user input.
    """

    #: Reported as ``meta.group_scheme`` ("isco-08", "soc-2018-major").
    name: str
    #: The group code of a matching occupation, or null for other nodes.
    code_expr: str
    #: The string a group's prefix must start, for ``search_group``.
    member_expr: str
    #: A user-given group code as that prefix, or None when it is not a group.
    prefix_for: Callable[[str], str | None]
    #: Code -> {"id", "label"} for the codes shown.
    names_for: Callable[[Session, list[str]], Mapping[str, Mapping[str, Any]]]


@dataclass(frozen=True)
class LocateConfig:
    """Everything that differs between suites' Locate."""

    #: A suite name from the contract (``SuiteName``); add yours there first.
    source: SuiteName
    #: The umbrella label every node of the suite carries ("EscoNode").
    node_label: str
    occupation_label: str
    #: Labels searched when no ``kind`` is given.
    default_labels: tuple[str, ...]
    #: ``kind`` -> one label. Its values are the only labels ever put in Cypher.
    kind_aliases: Mapping[str, str]
    fulltext_index: str
    vector_index: str
    confidences: Confidences = field(default_factory=Confidences)
    #: ``kind`` -> several labels ("skill" on O*NET also searches tasks).
    kind_expansions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Node properties returned beside the common ones ("uri").
    extra_node_fields: tuple[str, ...] = ()
    #: Properties to fall back on, in order, when a node has no ``source_id``.
    source_id_fallbacks: tuple[str, ...] = ()
    groups: GroupScheme | None = None
    #: Query -> the ``code`` values it names ("15-1252" -> ["15-1252.00"]), or
    #: [] when it is not a code. A code is looked up before any label.
    codes_for: Callable[[str], list[str]] | None = None
    search_limit: int = 25
    scan_cap: int = 5_000
    #: Shorter terms skip the full-text index (a wildcard that wide costs more).
    min_wildcard_term: int = 3
    vector_k: int = 25
    #: Neo4j cosine score is (1 + cos) / 2. Measured with all-MiniLM-L6-v2 over
    #: the full graphs (2026-09-30): gibberish tops out at 0.732 (ESCO) / 0.703
    #: (O*NET); natural-language job descriptions start at 0.739 / 0.742.
    vector_min_score: float = 0.74

    @property
    def searchable_labels(self) -> frozenset[str]:
        expanded = {label for labels in self.kind_expansions.values() for label in labels}
        return frozenset({*self.kind_aliases.values(), *self.default_labels, *expanded})


# -- query text ---------------------------------------------------------------

# Split on anything that is not a letter or digit. The terms line up with what
# the analyzer indexes, and none of them can carry a Lucene metacharacter, so
# wildcard terms built from them never need escaping.
_TERM_SPLIT = re.compile(r"[\W_]+", re.UNICODE)
ACRONYM_RE = re.compile(r"\b[A-Z]{2,3}\b")
#: More required wildcard terms than any title has; well under Lucene's 1024.
MAX_INFIX_TERMS = 32


def query_terms(q: str) -> list[str]:
    return [term for term in _TERM_SPLIT.split(q.lower()) if term]


def lucene_phrase(q: str) -> str | None:
    """Quoted phrase for the exact tiers, or None when nothing is indexable.

    None means the caller must scan rather than conclude there is no match:
    "+++" indexes to no terms at all.
    """
    if not query_terms(q):
        return None
    escaped = q.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def lucene_infix(q: str, min_term: int) -> str | None:
    """Every term required and wrapped in ``*``: a superset of the word-start
    predicate, which Cypher re-applies. None when a term is too short to pay."""
    terms = query_terms(q)
    if not terms or any(len(term) < min_term for term in terms):
        return None
    if len(terms) > MAX_INFIX_TERMS:
        # A pasted paragraph: past Lucene's clause limit the query raises. The
        # scan answers instead (and finds nothing, as no title is that long).
        return None
    return " ".join(f"+*{term}*" for term in terms)


# -- meaning search -------------------------------------------------------------


def embedding_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


@functools.lru_cache(maxsize=1)
def embedding_model() -> Any:
    """One model per process, shared by every suite (it is ~400 MB in memory)."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")


def embed_query(text: str) -> list[float] | None:
    """A unit-norm embedding for ``text``, or None without the local model."""
    if not embedding_available():
        return None
    vec = embedding_model().encode([text], normalize_embeddings=True)
    return vec[0].tolist()


def reciprocal_rank_fusion(
    result_lists: list[list[dict[str, Any]]], k: int = 60
) -> list[dict[str, Any]]:
    """Merge ranked lists by RRF, best first."""
    scores: dict[str, float] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for rows in result_lists:
        for rank, row in enumerate(rows):
            node_id = str(row.get("id") or "")
            if not node_id:
                continue
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank + 1)
            by_id.setdefault(node_id, row)
    return sorted(by_id.values(), key=lambda r: -scores[str(r.get("id") or "")])


def alias_needs_second_opinion(q: str, *row_sets: list[dict[str, Any]]) -> bool:
    """An acronym query that no title contains is not trusted on aliases alone.

    ESCO lists "AI engineer" as an alias of "animal artificial insemination
    technician", and "QA tester" only inside an alias of "localiser"; "HR
    manager" is a correct alias of "human resources manager". Only the meaning
    search can tell them apart.
    """
    if not ACRONYM_RE.search(q):
        return False
    needle = q.casefold()
    return not any(
        needle in (row.get("pref_label") or "").casefold() for rows in row_sets for row in rows
    )


def _sort_key(row: dict[str, Any]) -> tuple[int, str]:
    """Mirror the Cypher ordering: shortest label first, then id."""
    return len(row.get("pref_label") or ""), str(row.get("id") or "")


# -- Cypher ---------------------------------------------------------------------


class LocateQueries:
    """The Cypher of one suite's Locate, built once from its config."""

    def __init__(self, config: LocateConfig) -> None:
        self.config = config
        fields = ", ".join(f"{name}: n.{name}" for name in config.extra_node_fields)
        extra = f"{fields}, " if fields else ""
        self.node_map = f"""{{
                id: n.id, pref_label: n.pref_label, source: n.source,
                source_id: n.source_id, {extra}kind: n.kind,
                code: n.code, description: n.description,
                alt_labels: n.alt_labels, labels: labels(n)
            }}"""
        fulltext_head = "CALL db.index.fulltext.queryNodes($index, $lucene) YIELD node AS n"
        scan_head = f"MATCH (n:{config.node_label})"
        alias_body = f"""
WHERE n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
  AND ($q IN coalesce(n.alt_labels, []) OR toLower(n.pref_label) = toLower($q))
WITH n, ($q IN coalesce(n.alt_labels, [])) AS is_alt
ORDER BY size(n.pref_label), n.id
LIMIT $scan_cap
WITH collect({{node: {self.node_map}, is_alt: is_alt}}) AS rows
WITH [r IN rows WHERE r.is_alt | r.node] AS alt,
     [r IN rows WHERE NOT r.is_alt | r.node] AS cf
RETURN size(alt) AS alt_total, alt[0..$limit] AS alt_top,
       size(cf) AS cf_total, cf[0..$limit] AS cf_top
"""
        groups = config.groups
        member = groups.member_expr if groups else "''"
        code = groups.code_expr if groups else "null"
        contains_body = f"""
WHERE n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
  AND ($group_prefix IS NULL OR coalesce({member}, '') STARTS WITH $group_prefix)
  AND (
    n.pref_label =~ $word_start
    OR any(a IN coalesce(n.alt_labels, []) WHERE a =~ $word_start)
  )
// Titles that contain the query rank ahead of alias-only matches *before* the
// cut, or short unrelated titles with a matching alias crowd them out.
WITH n, CASE WHEN n.pref_label =~ $word_start THEN 0 ELSE 1 END AS alias_only
ORDER BY alias_only, size(n.pref_label), n.id
LIMIT $scan_cap
// Group codes of every title match, not just the top slice. Alias-only
// matches are left out ("nursery nurse" would put child care under "nurse").
WITH collect({self.node_map}) AS rows,
     collect(CASE WHEN alias_only = 0 THEN {code} END) AS group_codes
RETURN size(rows) AS total, rows[0..$limit] AS top, group_codes
"""
        self.fulltext_alias_or_casefold = fulltext_head + alias_body
        self.scan_alias_or_casefold = scan_head + alias_body
        self.fulltext_contains = fulltext_head + contains_body
        self.scan_contains = scan_head + contains_body
        # The Lucene score only orders this list for the fusion; it never
        # becomes a confidence.
        self.fulltext_keywords = f"""
CALL db.index.fulltext.queryNodes($index, $lucene) YIELD node AS n, score
WHERE n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
WITH n, score
ORDER BY score DESC, size(n.pref_label), n.id
LIMIT $limit
RETURN collect({self.node_map}) AS top
"""
        self.vector = f"""
CALL db.index.vector.queryNodes($index, $k, $embedding)
YIELD node AS n, score
WHERE score >= $min_score
  AND n.source = $source
  AND any(x IN labels(n) WHERE x IN $labels)
RETURN {self.node_map} AS node
LIMIT $limit
"""

    def exact_pref(self, labels: list[str]) -> str:
        """One index seek per concrete label, unioned in a single round trip.

        A label must be interpolated (Cypher cannot parameterise one), so only
        the suite's own labels are accepted.
        """
        arms = []
        for label in labels:
            if label not in self.config.searchable_labels:
                raise ValueError(f"label is not searchable: {label!r}")
            arms.append(
                f"MATCH (n:{label})\n"
                "WHERE n.source = $source AND n.pref_label = $q\n"
                f"RETURN {self.node_map} AS node\n"
                "LIMIT $scan_cap"
            )
        return "\nUNION\n".join(arms)


_QUERIES: dict[int, LocateQueries] = {}


def queries_for(config: LocateConfig) -> LocateQueries:
    """The Cypher for ``config``, built once (configs are module constants)."""
    built = _QUERIES.get(id(config))
    if built is None or built.config is not config:
        built = _QUERIES[id(config)] = LocateQueries(config)
    return built


# -- results --------------------------------------------------------------------


def record_to_node(config: LocateConfig, rec: dict[str, Any]) -> Node:
    labels = list(rec.get("labels") or [])
    kind = rec.get("kind") or (labels[0] if labels else "Node")
    source_id = rec.get("source_id")
    for fallback in config.source_id_fallbacks:
        source_id = source_id or rec.get(fallback)
    return Node(
        id=rec["id"],
        kind=str(kind),
        label=rec.get("pref_label") or "",
        source=config.source,
        source_id=source_id or "",
        properties={
            k: v
            for k, v in rec.items()
            if k not in {"id", "pref_label", "source", "source_id", "labels", "kind"}
            and v is not None
        },
    )


def locate_result(
    config: LocateConfig,
    rows: list[dict[str, Any]],
    total: int,
    confidence: float,
    method: str,
    evidence_suffix: str,
    notes: list[str],
    *,
    ambiguous: bool = False,
) -> ToolResult:
    """A Locate result that says how much of the match set it is showing.

    ``pruning`` carries the counts; ``truncated`` is the flag an agent can
    branch on without reading them.
    """
    candidates = [
        Candidate(node=record_to_node(config, row), confidence=confidence, method=method)
        for row in rows
    ]
    warnings = list(notes)
    if ambiguous:
        warnings.append("ambiguous")
    pruned = max(0, total - len(candidates))
    if pruned:
        warnings.append("truncated")
    if total >= config.scan_cap:
        # The total itself stopped at the cap; report it as a floor, not a fact.
        warnings.append("match_count_capped")
    return ToolResult(
        candidates=candidates,
        nodes=[candidate.node for candidate in candidates],
        pruning=PruningStats(
            considered=len(candidates) + pruned, returned=len(candidates), pruned=pruned
        ),
        warnings=warnings,
        evidence=[f"{config.source}:search:{evidence_suffix}"],
        meta={"limit": config.search_limit, "matches": total},
    )


def merged_result(
    config: LocateConfig,
    alt_rows: list[dict[str, Any]],
    contains_rows: list[dict[str, Any]],
    total: int,
    notes: list[str],
    evidence_suffix: str,
    *,
    meaning_rows: list[dict[str, Any]] | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> ToolResult:
    """A Locate result where each row keeps the tier that matched it.

    Exact alias hits keep ``exact_alt``, substring hits ``contains``, meaning
    hits ``hybrid_rrf``; deduped by id, the stronger tier winning. An alias
    that is exactly the query is never demoted to a guess.
    """
    conf = config.confidences
    tiers = (
        (alt_rows, conf.exact_alt, "exact_alt"),
        (contains_rows, conf.contains, "contains"),
        (meaning_rows or [], conf.hybrid, "hybrid_rrf"),
    )
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for rows, confidence, method in tiers:
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            candidates.append(
                Candidate(node=record_to_node(config, row), confidence=confidence, method=method)
            )
    # Exact aliases first, then contains, then meaning: one page, like every tier.
    candidates = candidates[: config.search_limit]
    warnings = [*notes, *extra_warnings]
    if len(candidates) > 1:
        warnings.append("ambiguous")
    pruned = max(0, total - len(candidates))
    if pruned:
        warnings.append("truncated")
    if total >= config.scan_cap:
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
        evidence=[f"{config.source}:search:{evidence_suffix}"],
        meta={"limit": config.search_limit, "matches": total},
    )


# -- the engine -----------------------------------------------------------------


class Locator:
    """Runs the tiers for one suite.

    ``embed`` is looked up per call so tests can replace a suite's
    ``_embed_query`` without loading a model.
    """

    def __init__(
        self,
        config: LocateConfig,
        session: Callable[[], Session],
        embed: Callable[[str], list[float] | None],
    ) -> None:
        self.config = config
        self.q = queries_for(config)
        self._session = session
        self._embed = embed

    def labels_for(self, kind: str | None) -> list[str] | None:
        """The labels a ``kind`` searches, or None when the suite has no such kind."""
        if not kind:
            return list(self.config.default_labels)
        normalized = kind.lower().replace(" ", "_")
        expanded = self.config.kind_expansions.get(normalized)
        if expanded is not None:
            return list(expanded)
        mapped = self.config.kind_aliases.get(normalized)
        return [mapped] if mapped is not None else None

    def search_nodes(self, text: str, kind: str | None = None) -> ToolResult:
        """Locate: free text to nodes with confidence. Never invents a hit."""
        q = (text or "").strip()
        if not q:
            return ToolResult(warnings=["empty_query"])
        labels = self.labels_for(kind)
        if labels is None:
            return ToolResult(warnings=[f"unknown_kind:{kind}"])
        conf = self.config.confidences
        source = self.config.source
        notes: list[str] = []
        with self._session() as session:
            # 0) a code ("15-1252.00", "2512") names one record, before any label
            codes = self.config.codes_for(q) if self.config.codes_for else []
            if codes:
                rows = self.match_code(session, labels, codes)
                if rows:
                    return locate_result(
                        self.config,
                        rows,
                        len(rows),
                        conf.exact_pref,
                        "exact_code",
                        f"exact_code:{q}",
                        notes,
                        ambiguous=len(rows) > 1,
                    )

            # 1) exact preferred label
            total, rows = self.match_exact_pref(session, labels, q)
            if rows:
                return locate_result(
                    self.config,
                    rows,
                    total,
                    conf.exact_pref,
                    "exact_pref",
                    f"exact_pref:{q}",
                    notes,
                )

            # 2+3) exact alias, then the preferred label apart from case.
            alt_rows, cf_total, cf_rows = self.match_exact_alias_or_casefold(
                session, labels, q, notes
            )
            if cf_total == 1:
                return locate_result(
                    self.config,
                    cf_rows,
                    cf_total,
                    conf.casefold_unique,
                    "casefold_pref",
                    f"casefold_pref:{q}",
                    notes,
                )
            if cf_rows:
                return locate_result(
                    self.config,
                    cf_rows,
                    cf_total,
                    conf.casefold_ambiguous,
                    "casefold_pref_ambiguous",
                    f"casefold_pref:{q}",
                    notes,
                    ambiguous=True,
                )

            # 4) word-start match, merged with the exact alias hits so a node
            # whose alias is the query ("nanny" → "nurse") cannot block the
            # titles that contain it ("registered nurse").
            total, rows, group_codes = self.match_contains(session, labels, q, notes)
            alt_ids = {row["id"] for row in alt_rows}
            if alt_rows:
                total = len(alt_rows) + sum(1 for row in rows if row["id"] not in alt_ids)
            if (alt_rows or rows) and alias_needs_second_opinion(q, alt_rows, rows):
                vector = self._embed(q)
                meaning_rows = (
                    self.match_vector(session, labels, vector, notes) if vector is not None else []
                )
                alias_ids = alt_ids | {row["id"] for row in rows}
                if meaning_rows and meaning_rows[0]["id"] not in alias_ids:
                    # The meaning search disagrees with the alias: offer both.
                    return merged_result(
                        self.config,
                        alt_rows,
                        rows,
                        # Every word-start match counts, not only the shown page.
                        total + sum(1 for row in meaning_rows if row["id"] not in alias_ids),
                        notes,
                        f"alias_unconfirmed:{q}",
                        meaning_rows=meaning_rows,
                        extra_warnings=("alias_unconfirmed",),
                    )
            if alt_rows:
                merged = merged_result(self.config, alt_rows, rows, total, notes, f"contains:{q}")
                return self.with_groups(session, merged, group_codes)
            if rows:
                found = locate_result(
                    self.config,
                    rows,
                    total,
                    conf.contains,
                    "contains",
                    f"contains:{q}",
                    notes,
                    ambiguous=total > 1,
                )
                return self.with_groups(session, found, group_codes)

            # 5) keyword list fused with the vector list; only when 1-4 found
            # nothing. Without a vector index or the model, keywords alone.
            keyword_rows = self.match_keywords(session, labels, q, notes)
            vector = self._embed(q)
            vector_rows = (
                self.match_vector(session, labels, vector, notes) if vector is not None else []
            )
            hybrid_rows = reciprocal_rank_fusion([keyword_rows, vector_rows])
            if not hybrid_rows:
                return ToolResult(
                    warnings=["not_found", *notes], evidence=[f"{source}:search:not_found:{q}"]
                )
            return locate_result(
                self.config,
                hybrid_rows[: self.config.search_limit],
                len(hybrid_rows),
                conf.hybrid,
                "hybrid_rrf",
                f"hybrid:{q}",
                notes,
                # A meaning-search hit is a guess, even a lone one: the user
                # confirms it, so it is never "the" answer.
                ambiguous=True,
            )

    def search_group(self, text: str, group: str) -> ToolResult:
        """Occupations matching ``text`` inside one group from ``meta.groups``."""
        q = (text or "").strip()
        code = (group or "").strip()
        if not q:
            return ToolResult(warnings=["empty_query"])
        scheme = self.config.groups
        prefix = scheme.prefix_for(code) if scheme is not None else None
        if prefix is None:
            return ToolResult(warnings=[f"unknown_group:{group}"])
        notes: list[str] = []
        with self._session() as session:
            total, rows, _ = self.match_contains(
                session, [self.config.occupation_label], q, notes, group_prefix=prefix
            )
        if not rows:
            return ToolResult(
                warnings=["not_found", *notes],
                evidence=[f"{self.config.source}:search_group:{code}:not_found:{q}"],
            )
        return locate_result(
            self.config,
            rows,
            total,
            self.config.confidences.contains,
            "contains",
            f"group:{code}:{q}",
            notes,
            ambiguous=total > 1,
        )

    def with_groups(self, session: Session, result: ToolResult, codes: list[str]) -> ToolResult:
        """Add ``meta.groups`` when the title matches span two groups or more."""
        scheme = self.config.groups
        if scheme is None:
            return result
        shown, total = top_groups(codes)
        if total < 2:
            return result
        names = scheme.names_for(session, [code for code, _ in shown])
        return attach_groups(result, shown, total, names, scheme.name)

    # -- retrieval: each returns the total as well as the capped rows ----------

    def match_code(
        self, session: Session, labels: list[str], codes: list[str]
    ) -> list[dict[str, Any]]:
        """Nodes whose ``code`` is one of ``codes`` (passed as a parameter)."""
        result = session.run(
            f"MATCH (n:{self.config.node_label}) "
            "WHERE n.source = $source AND n.code IN $codes "
            "AND any(x IN labels(n) WHERE x IN $labels) "
            f"RETURN {self.q.node_map} AS node LIMIT $limit",
            source=self.config.source,
            codes=codes,
            labels=labels,
            limit=self.config.search_limit,
        )
        rows = [dict(record["node"]) for record in result]
        rows.sort(key=_sort_key)
        return rows

    def match_exact_pref(
        self, session: Session, labels: list[str], q: str
    ) -> tuple[int, list[dict[str, Any]]]:
        result = session.run(
            self.q.exact_pref(labels), q=q, source=self.config.source, scan_cap=self.config.scan_cap
        )
        rows = [dict(record["node"]) for record in result]
        rows.sort(key=_sort_key)
        return len(rows), rows[: self.config.search_limit]

    def match_exact_alias_or_casefold(
        self, session: Session, labels: list[str], q: str, notes: list[str]
    ) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
        """Exact alias and casefold title in one round trip (alias rows, cf total, cf rows).

        The phrase query is a superset of both predicates, which Cypher then
        re-applies: same answer as a scan, over a few hundred candidates.
        """
        params: dict[str, Any] = {
            "q": q,
            "labels": labels,
            "source": self.config.source,
            "limit": self.config.search_limit,
            "scan_cap": self.config.scan_cap,
        }
        phrase = lucene_phrase(q)
        record = None
        if phrase is not None:
            record = self.run_fulltext(
                session,
                self.q.fulltext_alias_or_casefold,
                notes,
                lucene=phrase,
                index=self.config.fulltext_index,
                **params,
            )
        if record is None:
            # No indexable terms, or no full-text index: scan. Slower, same answer.
            record = session.run(self.q.scan_alias_or_casefold, **params).single()
        if record is None:
            return [], 0, []
        return (
            [dict(row) for row in record["alt_top"]],
            int(record["cf_total"]),
            [dict(row) for row in record["cf_top"]],
        )

    def match_contains(
        self,
        session: Session,
        labels: list[str],
        q: str,
        notes: list[str],
        group_prefix: str | None = None,
    ) -> tuple[int, list[dict[str, Any]], list[str]]:
        """Word-start match on titles and aliases: (total, rows, group codes)."""
        params: dict[str, Any] = {
            "word_start": word_start_pattern(q),
            "group_prefix": group_prefix,
            "labels": labels,
            "source": self.config.source,
            "limit": self.config.search_limit,
            "scan_cap": self.config.scan_cap,
        }
        lucene = lucene_infix(q, self.config.min_wildcard_term)
        record = None
        if lucene is not None:
            record = self.run_fulltext(
                session,
                self.q.fulltext_contains,
                notes,
                lucene=lucene,
                index=self.config.fulltext_index,
                **params,
            )
        if record is None:
            record = session.run(self.q.scan_contains, **params).single()
        if record is None:
            return 0, [], []
        codes = [str(code) for code in (record.get("group_codes") or []) if code]
        return int(record["total"]), [dict(row) for row in record["top"]], codes

    def match_keywords(
        self, session: Session, labels: list[str], q: str, notes: list[str]
    ) -> list[dict[str, Any]]:
        """Labels sharing topic words with ``q``, most shared words first."""
        lucene = keyword_query(q)
        if lucene is None:
            return []
        record = self.run_fulltext(
            session,
            self.q.fulltext_keywords,
            notes,
            lucene=lucene,
            labels=labels,
            source=self.config.source,
            index=self.config.fulltext_index,
            # Over-fetch: the shared-words filter below drops weak hits.
            limit=self.config.search_limit * 4,
        )
        if record is None:
            return []
        stems = topic_stems(q)
        return [
            dict(row)
            for row in record["top"]
            if enough_words(stems, [row.get("pref_label") or "", *(row.get("alt_labels") or [])])
        ]

    def match_vector(
        self, session: Session, labels: list[str], embedding: list[float], notes: list[str]
    ) -> list[dict[str, Any]]:
        """Nearest labels by meaning; [] (with a note) when the index is missing."""
        try:
            result = session.run(
                self.q.vector,
                index=self.config.vector_index,
                k=self.config.vector_k,
                min_score=self.config.vector_min_score,
                embedding=embedding,
                source=self.config.source,
                labels=labels,
                limit=self.config.search_limit,
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
    def run_fulltext(session: Session, cypher: str, notes: list[str], **params: Any) -> Any:
        """Run a full-text query, or None when the index is not there.

        A graph loaded before the index existed keeps working: the caller scans
        and the note says why the call was slow.
        """
        try:
            return session.run(cypher, **params).single()
        except ClientError as exc:
            if "no such fulltext" not in str(exc).lower():
                raise
            if "fulltext_index_missing" not in notes:
                notes.append("fulltext_index_missing")
            return None
