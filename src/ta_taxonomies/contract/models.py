"""Shared Pydantic models for the suite contract (typed tool I/O).

Use case: define the only data shapes TA-agents (and other callers) should
depend on when talking to any suite — Node, Edge, Candidate, ToolResult.

Why it exists: keeps Locate/Connect/Pathfind results structured (ids,
confidence, warnings, evidence pointers) instead of free-form prose or
Neo4j-specific types. Suites fill these models; agents consume them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SuiteName = Literal["esco", "onet", "sfia", "bls", "jobtech"]


class Node(BaseModel):
    """A graph node returned by suite tools.

    Example::

        Node(
            id="esco:occupation:f2b15a0e-e65a-438a-affb-29b9d50b77d1",
            kind="Occupation",
            label="software developer",
            source="esco",
            source_id="http://data.europa.eu/esco/occupation/f2b15a0e-e65a-438a-affb-29b9d50b77d1",
            properties={"code": "2512.4"},
        )
    """

    id: str = Field(..., description="Suite-scoped id, e.g. esco:occupation:…")
    kind: str = Field(..., description="Canonical or suite kind: Occupation, Skill, …")
    label: str
    source: SuiteName
    source_id: str = Field(..., description="Native source identifier (usually URI)")
    properties: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    """A graph edge returned by suite tools.

    Example::

        Edge(
            type="HAS_SKILL",
            from_id="esco:occupation:f2b15a0e-e65a-438a-affb-29b9d50b77d1",
            to_id="esco:skill:fed5b267-73fa-461d-9f69-827c78beb39d",
            properties={"relation_type": "essential"},
        )
    """

    type: str
    from_id: str
    to_id: str
    properties: dict[str, Any] = Field(default_factory=dict)


class Path(BaseModel):
    """An ordered graph route with the relationships needed to explain it."""

    node_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Ordered suite-scoped node IDs in the route",
    )
    edges: list[Edge] = Field(
        default_factory=list,
        description="Ordered graph edges connecting adjacent route nodes",
    )

    @model_validator(mode="after")
    def validate_route(self) -> Path:
        """Require one correctly oriented edge for every adjacent node pair."""
        expected_edges = len(self.node_ids) - 1
        if len(self.edges) != expected_edges:
            raise ValueError(
                f"path with {len(self.node_ids)} nodes requires exactly {expected_edges} edges"
            )

        for index, edge in enumerate(self.edges):
            expected_from = self.node_ids[index]
            expected_to = self.node_ids[index + 1]
            actual_endpoints = {edge.from_id, edge.to_id}
            expected_endpoints = {expected_from, expected_to}
            if actual_endpoints != expected_endpoints:
                raise ValueError(f"edge {index} must connect {expected_from!r} to {expected_to!r}")

        return self


class PolicyRef(BaseModel):
    """Stable identity of a declared path-scoring policy."""

    name: str = Field(..., min_length=1, description="Human-readable policy name")
    version: str = Field(..., min_length=1, description="Policy version identifier")


class ScoredPath(BaseModel):
    """A path score together with the declared policy that produced it."""

    path: Path
    score: float
    policy: PolicyRef


class PruningStats(BaseModel):
    """Counts from a bounded result without exposing what was discarded.

    Used by any tool that caps what it returns: ``enumerate_paths`` for pruned
    routes, ``search_nodes`` for matches beyond the result limit. ``considered``
    is what the tool found, ``returned`` what the caller got. A capped answer
    that does not say it was capped reads as a complete one, which is the
    failure this exists to prevent.
    """

    considered: int = Field(..., ge=0)
    returned: int = Field(..., ge=0)
    pruned: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> PruningStats:
        if self.considered != self.returned + self.pruned:
            raise ValueError("considered must equal returned plus pruned")
        return self


class Candidate(BaseModel):
    """A Locate / search_nodes hit (node + how it was matched).

    Example::

        Candidate(
            node=Node(
                id="esco:occupation:f2b15a0e-e65a-438a-affb-29b9d50b77d1",
                kind="Occupation",
                label="software developer",
                source="esco",
                source_id="http://data.europa.eu/esco/occupation/f2b15a0e-e65a-438a-affb-29b9d50b77d1",
            ),
            confidence=0.95,
            method="exact_pref",
        )
    """

    node: Node
    confidence: float = Field(..., ge=0.0, le=1.0)
    method: str = Field(..., description="exact_pref | exact_alt | contains | …")


class ToolResult(BaseModel):
    """Typed envelope for suite tool responses.

    Example (Locate / search_nodes)::

        ToolResult(
            candidates=[...],  # list[Candidate]
            nodes=[...],       # same nodes, flat list
            warnings=[],
            evidence=["esco:search:exact_pref:software developer"],
        )

    Example (not found)::

        ToolResult(warnings=["not_found"])
    """

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    paths: list[Path] = Field(
        default_factory=list,
        description="Ordered nodes and edges returned by enumerate_paths",
    )
    scored_paths: list[ScoredPath] = Field(
        default_factory=list,
        description="Ranked paths with scores and the policy used",
    )
    pruning: PruningStats | None = Field(
        default=None,
        description="Counts from a bounded result (pruned paths, truncated matches)",
    )
    warnings: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(
        default_factory=list,
        description="Pointers / citations (not licensed payloads)",
    )
    meta: dict[str, Any] = Field(default_factory=dict)
