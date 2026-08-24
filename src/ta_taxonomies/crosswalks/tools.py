"""Query surface for cross-taxonomy links.

Use case: answer "this ESCO occupation -- which O*NET occupation is it?" and
its inverse, in the contract's ``ToolResult`` shape, with the citation attached
to every edge.

Why it exists: the crosswalk data is only useful if callers cannot get an
answer without also getting who published it. Every result here carries
``evidence`` pointers, and every edge carries the source key, the match
strength as published, and the method by which the mapping was produced.

The default answer is published data only. Project claims are reachable only
through ``include_claims=True``, and when they are included the result carries
a warning naming them, so a claim can never be read as a published fact by a
caller who was not paying attention.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver

from ta_taxonomies.contract.models import Edge, Node, ToolResult
from ta_taxonomies.crosswalks.config import (
    LABEL_CROSSWALK_SOURCE,
    LABEL_NO_LINK,
    REL_ASSERTED_CORRESPONDS_TO,
    REL_CORRESPONDS_TO,
    REL_RECORDED_NO_LINK,
    TRAVERSABLE_RELS,
)
from ta_taxonomies.crosswalks.models import ClaimStatus

# Only ACCEPTED claims speak for the project. Anything earlier is work in
# progress and must not surface in an answer, even an opt-in one.
#
# Derived from the enum rather than repeating the string. A parallel literal is
# a second source of truth: rename the enum member's value and this query would
# filter on a status no claim carries, so every claim would silently vanish
# from opt-in answers. Fail-closed, but silent either way.
_ANSWERABLE_CLAIM_STATUS = ClaimStatus.ACCEPTED.value


def traversable_pattern() -> str:
    """Cypher relationship alternation for the declared-traversable types.

    The published-data query is built from ``TRAVERSABLE_RELS`` rather than
    naming ``CORRESPONDS_TO`` inline. The two produce identical Cypher today,
    since the set has one member -- the point is that the config actually
    governs. A constant that reads as a safety control while the code hardcodes
    its own answer is worse than no constant: it invites a reviewer to check the
    config and conclude something the code does not do.
    """
    return "|".join(sorted(TRAVERSABLE_RELS))


def _node_from_record(data: dict[str, Any]) -> Node:
    return Node(
        id=data["id"],
        kind=data.get("kind") or "Occupation",
        label=data.get("pref_label") or "",
        source=data["source"],
        source_id=data.get("source_id") or data["id"],
        properties={"code": data.get("code")} if data.get("code") else {},
    )


class Crosswalks:
    """Read-only cross-taxonomy queries over a loaded graph."""

    def __init__(self, driver: Driver, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    # -- published correspondences -----------------------------------------

    def counterparts(
        self,
        node_id: str,
        *,
        to_suite: str | None = None,
        include_claims: bool = False,
    ) -> ToolResult:
        """Return the counterparts of ``node_id`` in another taxonomy.

        Traverses in both directions: a crosswalk table has a direction on
        paper (ESCO to O*NET), but the correspondence it records is symmetric,
        and forcing callers to know which way the file was written would be a
        leak of the file's layout into the query surface.

        Warnings are part of the answer, not decoration. A caller gets told
        when nothing was found, when an absence was explicitly recorded, when
        the mapping was model-assisted rather than deterministic, and when
        project claims are mixed into the result.
        """
        result = ToolResult()
        with self._driver.session(database=self._database) as session:
            records = list(
                session.run(
                    f"""
                    MATCH (a {{id: $node_id}})-[r:{traversable_pattern()}]-(b)
                    WHERE $to_suite IS NULL OR b.source = $to_suite
                    OPTIONAL MATCH (s:{LABEL_CROSSWALK_SOURCE} {{key: r.source_key}})
                    RETURN b {{.id, .kind, .source, .source_id, .pref_label, .code}} AS node,
                           type(r) AS rel_type,
                           r.source_key AS source_key,
                           r.strength AS strength,
                           startNode(r).id AS from_id,
                           endNode(r).id AS to_id,
                           s.source_url AS source_url,
                           s.method AS method,
                           s.source_version AS source_version
                    """,
                    node_id=node_id,
                    to_suite=to_suite,
                )
            )
            for record in records:
                result.nodes.append(_node_from_record(record["node"]))
                result.edges.append(
                    Edge(
                        type=record["rel_type"],
                        from_id=record["from_id"],
                        to_id=record["to_id"],
                        properties={
                            "source_key": record["source_key"],
                            "strength": record["strength"],
                            "method": record["method"],
                            "source_version": record["source_version"],
                            "asserted_by_project": False,
                        },
                    )
                )
                pointer = f"{record['source_key']}:{record['source_url']}"
                if pointer not in result.evidence:
                    result.evidence.append(pointer)
                if record["method"] and record["method"].startswith("model_assisted"):
                    warning = f"{record['source_key']}: published mapping is model-assisted"
                    if warning not in result.warnings:
                        result.warnings.append(warning)
                if record["strength"] == "unspecified":
                    warning = f"{record['source_key']}: source publishes no match strength"
                    if warning not in result.warnings:
                        result.warnings.append(warning)

            if include_claims:
                result = self._add_claims(session, node_id, to_suite, result)

            # An explicitly recorded absence is a different answer from silence,
            # and the caller deserves to be told which one they got.
            for record in session.run(
                f"""
                MATCH (a {{id: $node_id}})-[:{REL_RECORDED_NO_LINK}]->(n:{LABEL_NO_LINK})
                WHERE $to_suite IS NULL OR n.to_suite = $to_suite
                RETURN n.reason AS reason, n.checked_against AS checked_against
                """,
                node_id=node_id,
                to_suite=to_suite,
            ):
                result.warnings.append(f"no_link[{record['checked_against']}]: {record['reason']}")

        if not result.nodes and not result.warnings:
            result.warnings.append("not_found")
        result.meta["node_id"] = node_id
        result.meta["to_suite"] = to_suite
        result.meta["claims_included"] = include_claims
        return result

    def _add_claims(
        self,
        session: Any,
        node_id: str,
        to_suite: str | None,
        result: ToolResult,
    ) -> ToolResult:
        """Append accepted project claims, flagged as such on every edge."""
        records = list(
            session.run(
                f"""
                MATCH (a {{id: $node_id}})-[r:{REL_ASSERTED_CORRESPONDS_TO}]-(b)
                WHERE (($to_suite IS NULL) OR b.source = $to_suite)
                  AND r.status = $status
                RETURN b {{.id, .kind, .source, .source_id, .pref_label, .code}} AS node,
                       r.status AS status, r.owner AS owner, r.rationale AS rationale,
                       startNode(r).id AS from_id, endNode(r).id AS to_id
                """,
                node_id=node_id,
                to_suite=to_suite,
                status=_ANSWERABLE_CLAIM_STATUS,
            )
        )
        for record in records:
            result.nodes.append(_node_from_record(record["node"]))
            result.edges.append(
                Edge(
                    type=REL_ASSERTED_CORRESPONDS_TO,
                    from_id=record["from_id"],
                    to_id=record["to_id"],
                    properties={
                        "status": record["status"],
                        "owner": record["owner"],
                        "rationale": record["rationale"],
                        "asserted_by_project": True,
                    },
                )
            )
        if records:
            result.warnings.append(
                f"{len(records)} of these correspondences are asserted by this project, "
                "not published by any taxonomy authority"
            )
        return result

    # -- coverage ----------------------------------------------------------

    def coverage(self, source_key: str) -> dict[str, Any]:
        """Report what a loaded crosswalk actually covers.

        Deliberately reports the denominator alongside the numerator. "2,959
        occupations cross" is a number; "2,959 of 3,039" is a fact.
        """
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"""
                MATCH (s:{LABEL_CROSSWALK_SOURCE} {{key: $key}})
                OPTIONAL MATCH ()-[r:{REL_CORRESPONDS_TO} {{source_key: $key}}]->()
                WITH s, count(r) AS edges
                OPTIONAL MATCH (a)-[r2:{REL_CORRESPONDS_TO} {{source_key: $key}}]->()
                WITH s, edges, count(DISTINCT a) AS sources
                OPTIONAL MATCH ()-[r3:{REL_CORRESPONDS_TO} {{source_key: $key}}]->(b)
                WITH s, edges, sources, count(DISTINCT b) AS targets
                OPTIONAL MATCH (n:{LABEL_NO_LINK} {{checked_against: $key}})
                RETURN s.title AS title, s.source_version AS source_version,
                       s.method AS method, s.source_url AS source_url,
                       s.retrieved_on AS retrieved_on,
                       edges, sources, targets, count(n) AS no_links
                """,
                key=source_key,
            ).single()
        if record is None:
            return {"source_key": source_key, "loaded": False}
        return {
            "source_key": source_key,
            "loaded": True,
            "title": record["title"],
            "source_version": record["source_version"],
            "method": record["method"],
            "source_url": record["source_url"],
            "retrieved_on": str(record["retrieved_on"]),
            "correspondences": record["edges"],
            "distinct_from_nodes": record["sources"],
            "distinct_to_nodes": record["targets"],
            "recorded_no_links": record["no_links"],
        }
