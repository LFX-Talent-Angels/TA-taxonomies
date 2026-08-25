"""Typed core for cross-taxonomy links: what someone published vs what we assert.

Use case: every cross-suite correspondence in this repo is one of exactly two
things, and callers must be able to tell them apart without reading prose --
``PublishedCorrespondence`` (transcribed from a correspondence table a named
authority published) or ``AssertedCorrespondence`` (this project's own claim,
with an owner and a lifecycle).

Why it exists: ADR-0006 decision 4 rules out semantic fusion of skills across
taxonomies and directs us to the official crosswalks that already exist. The
risk that rule guards against is not malice, it is *drift*: a plausible mapping
gets generated, lands in the graph, and three months later nobody can say
whether a row came from the European Commission or from a model we ran. So the
distinction is enforced by the type system rather than by a boolean anyone can
forget to set:

- ``PublishedCorrespondence`` cannot be constructed without ``Provenance``
  (publisher, URL, version, retrieval date, licence).
- ``AssertedCorrespondence`` cannot be constructed without an ``owner``, a
  ``rationale`` and a ``status``.

There is deliberately no way to build one that is neither, and no field that
turns one into the other.

Orthogonal to *who* published a mapping is *how* it was produced. A published
crosswalk can still be model-generated -- the ESCO/O*NET occupation crosswalk
is, by its publishers' own account. ``MappingMethod`` records that separately
so "official" is never silently read as "deterministic".
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ta_taxonomies.contract.models import SuiteName


class MappingMethod(StrEnum):
    """How a correspondence was produced -- independent of who published it.

    Kept separate from publisher identity on purpose. "Official" answers *who
    stands behind this*; it does not answer *how much to trust a single row*.
    """

    DETERMINISTIC = "deterministic"
    """Follows mechanically from code structure; reproducible by anyone.

    Example: an O*NET-SOC code's SOC parent is the substring before the dot.
    """

    EXPERT_MAPPING = "expert_mapping"
    """Built by human classification experts, e.g. a statistical agency."""

    MODEL_ASSISTED_VALIDATED = "model_assisted_validated"
    """A model proposed candidates and humans reviewed and signed them off."""

    MODEL_ASSISTED_UNVALIDATED = "model_assisted_unvalidated"
    """A model proposed candidates and the publisher states they were not
    put through its quality-assurance or validation process."""

    UNDOCUMENTED = "undocumented"
    """The publisher did not describe the method. Not a synonym for the others."""


class MatchStrength(StrEnum):
    """Relation between the two concepts, in the vocabulary the mapping used.

    ``UNSPECIFIED`` is a first-class value, not a default to shrug at: the
    ESCO/O*NET file that O*NET distributes has no strength column even though
    the published methodology defines one. Recording that we were not told is
    honest; picking a value to fill the gap would be invention.
    """

    EXACT = "exact"
    NARROWER = "narrower"
    BROADER = "broader"
    CLOSE = "close"
    RELATED = "related"
    UNSPECIFIED = "unspecified"


class ClaimStatus(StrEnum):
    """Lifecycle of a correspondence this project asserts itself.

    Order is meaningful: a claim earns trust by moving right, and only
    ``ACCEPTED`` is fit to answer a user-facing question as project opinion.
    """

    OBSERVED = "observed"
    """Something in the data suggested a link. Nobody has argued for it yet."""

    PROPOSED = "proposed"
    """A named owner is arguing for it and has written down why."""

    REVIEWED = "reviewed"
    """A second person who is not the owner has read the rationale."""

    ACCEPTED = "accepted"
    """The project stands behind it. Still ours, still not source data."""

    DEPRECATED = "deprecated"
    """Withdrawn. Kept, never deleted, so downstream answers stay explicable."""


# Transitions allowed on a claim. Modelled as data rather than as branches so
# the rule can be read, tested and cited in review without tracing code.
#
# Two deliberate properties:
#   - DEPRECATED is reachable from every live state (anything can be withdrawn)
#     and can go back to PROPOSED (revived claims re-argue their case; they do
#     not silently resume the trust level they had before withdrawal).
#   - No transition skips REVIEWED on the way to ACCEPTED. A claim cannot
#     become project opinion without a second pair of eyes.
CLAIM_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.OBSERVED: frozenset({ClaimStatus.PROPOSED, ClaimStatus.DEPRECATED}),
    ClaimStatus.PROPOSED: frozenset({ClaimStatus.REVIEWED, ClaimStatus.DEPRECATED}),
    ClaimStatus.REVIEWED: frozenset(
        {ClaimStatus.ACCEPTED, ClaimStatus.PROPOSED, ClaimStatus.DEPRECATED}
    ),
    ClaimStatus.ACCEPTED: frozenset({ClaimStatus.DEPRECATED, ClaimStatus.REVIEWED}),
    ClaimStatus.DEPRECATED: frozenset({ClaimStatus.PROPOSED}),
}


def can_transition(current: ClaimStatus, target: ClaimStatus) -> bool:
    """Whether a claim may move from ``current`` to ``target``."""
    return target in CLAIM_TRANSITIONS[current]


class Provenance(BaseModel):
    """Where a published correspondence came from, precisely enough to re-fetch.

    Every field is required. A correspondence whose origin cannot be stated in
    full is not a published correspondence -- it is a claim, and belongs in
    ``AssertedCorrespondence`` where its owner is on the record.

    Example::

        Provenance(
            key="esco_onet_2019",
            title="ESCO to O*NET-SOC 2019 crosswalk",
            publishers=("European Commission, DG EMPL", "US DOL/ETA, O*NET"),
            source_url="https://www.onetcenter.org/crosswalks/esco/ESCO_to_ONET-SOC.xlsx",
            source_version="O*NET-SOC 2019",
            retrieved_on=date(2026, 8, 24),
            license="CC BY 4.0 (O*NET); ESCO terms with attribution",
            method=MappingMethod.MODEL_ASSISTED_VALIDATED,
        )
    """

    model_config = ConfigDict(frozen=True)

    key: str = Field(
        ...,
        min_length=1,
        pattern=r"^[a-z0-9_]+$",
        description="Stable short id used as the graph MERGE key, e.g. esco_onet_2019",
    )
    title: str = Field(..., min_length=1, description="The table's published name")
    publishers: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description="Every organisation that stands behind the table, not just the host",
    )
    source_url: str = Field(..., min_length=1, description="Where the file was fetched from")
    source_version: str = Field(
        ...,
        min_length=1,
        description="Version the publisher names, e.g. 'O*NET-SOC 2019'. Never 'latest'.",
    )
    retrieved_on: date = Field(
        ...,
        description="When we fetched it. Access terms move; ADR-0006 dates every check.",
    )
    license: str = Field(..., min_length=1, description="Terms under which we may use it")
    method: MappingMethod = Field(
        ...,
        description="How the publisher says the mapping was produced",
    )
    caveats: tuple[str, ...] = Field(
        default=(),
        description="Limitations the publisher states, or that we measured on the file",
    )

    @model_validator(mode="after")
    def reject_unpinned_version(self) -> Provenance:
        """ADR-0006 decision 6: pinned snapshots, never a live moving target."""
        if self.source_version.strip().lower() in {"latest", "current", "head"}:
            raise ValueError(
                f"source_version {self.source_version!r} is not pinned; "
                "record the version the publisher names"
            )
        return self


class _CorrespondenceBase(BaseModel):
    """Endpoints shared by both kinds of link. Not used directly."""

    model_config = ConfigDict(frozen=True)

    from_id: str = Field(..., min_length=1, description="Suite-scoped id, e.g. esco:occupation:…")
    to_id: str = Field(..., min_length=1, description="Suite-scoped id, e.g. onet:occupation:…")
    from_suite: SuiteName
    to_suite: SuiteName

    @model_validator(mode="after")
    def validate_endpoints(self) -> _CorrespondenceBase:
        """Endpoints must be suite-scoped, cross-suite, and distinct.

        A crosswalk edge inside one suite is not a crosswalk: it is a suite
        relationship that belongs in that suite's loader (ARCHITECTURE.md,
        "Suites never import each other"). Catching it here stops within-suite
        structure from leaking into the cross-taxonomy layer.
        """
        for field, value, suite in (
            ("from_id", self.from_id, self.from_suite),
            ("to_id", self.to_id, self.to_suite),
        ):
            if not value.startswith(f"{suite}:"):
                raise ValueError(f"{field}={value!r} is not scoped to suite {suite!r}")
        if self.from_suite == self.to_suite:
            raise ValueError(
                f"both endpoints are in suite {self.from_suite!r}; "
                "a within-suite link belongs in that suite, not in crosswalks/"
            )
        if self.from_id == self.to_id:
            raise ValueError("from_id and to_id are identical")
        return self


class PublishedCorrespondence(_CorrespondenceBase):
    """A row transcribed from a correspondence table someone else published.

    We copy it; we do not judge it. If the publisher gives no match strength we
    record ``UNSPECIFIED`` rather than deriving one, because deriving one would
    make this an assertion wearing a citation.

    Example::

        PublishedCorrespondence(
            from_id="esco:occupation:f2b1…",
            to_id="onet:occupation:15-1252.00",
            from_suite="esco",
            to_suite="onet",
            strength=MatchStrength.UNSPECIFIED,
            provenance=ESCO_ONET_2019,
            source_row={"ESCO/ISCO Code": "2512.4", "O*NET-SOC 2019 Code": "15-1252.00"},
        )
    """

    kind: Literal["published"] = "published"

    strength: MatchStrength = Field(
        default=MatchStrength.UNSPECIFIED,
        description="As published. UNSPECIFIED means the table has no strength column.",
    )
    provenance: Provenance = Field(..., description="Required. No provenance, no publication.")
    source_row: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The identifying cells of the originating row, for audit. Identifiers and "
            "codes only -- pointer, not payload (ADR-0006 decision 7)."
        ),
    )


class AssertedCorrespondence(_CorrespondenceBase):
    """A correspondence this project asserts on its own authority.

    This is the type that lets the project stop being a reader of other
    people's taxonomies and start having an opinion. Nothing populates it yet
    -- it exists so that the day someone wants to link an ESCO skill to an
    O*NET element, there is a place to put it that is structurally incapable of
    being mistaken for source data.

    Three required fields carry that weight: a named ``owner`` (a claim with no
    owner is a rumour), a written ``rationale`` (so review has something to
    review), and a ``status`` that starts at ``OBSERVED`` and only reaches
    ``ACCEPTED`` through ``REVIEWED``.

    Example::

        AssertedCorrespondence(
            from_id="esco:skill:abc…",
            to_id="onet:element:2.B.1.a",
            from_suite="esco",
            to_suite="onet",
            status=ClaimStatus.PROPOSED,
            owner="mentee-name",
            rationale=(
                "ESCO 'active listening' and O*NET 2.B.1.a 'Active Listening' share "
                "a definition almost verbatim; both are basic social skills."
            ),
            asserted_on=date(2026, 9, 1),
        )
    """

    kind: Literal["asserted"] = "asserted"

    status: ClaimStatus = Field(
        default=ClaimStatus.OBSERVED,
        description="Lifecycle position. Only ACCEPTED speaks for the project.",
    )
    owner: str = Field(
        ...,
        min_length=1,
        description="Person accountable for this claim. Required; a claim needs a name on it.",
    )
    rationale: str = Field(
        ...,
        min_length=1,
        description="Why this link is asserted. Required; review needs something to read.",
    )
    asserted_on: date | None = Field(
        default=None,
        description="When the claim was first recorded",
    )
    reviewed_by: str | None = Field(
        default=None,
        description="Who reviewed it. Must differ from owner (see validator).",
    )
    supersedes: tuple[str, ...] = Field(
        default=(),
        description="Claim ids this one replaces. Deprecated claims are kept, not deleted.",
    )
    evidence: tuple[str, ...] = Field(
        default=(),
        description="Pointers supporting the claim (URLs, node ids) -- never licensed prose",
    )

    @model_validator(mode="after")
    def validate_review(self) -> AssertedCorrespondence:
        """Self-review does not count, and later states require a reviewer.

        The whole value of the lifecycle is that ``ACCEPTED`` means someone
        other than the author looked. Letting an owner review their own claim
        would make the status a formality.
        """
        if self.reviewed_by is not None and self.reviewed_by == self.owner:
            raise ValueError(
                f"reviewed_by must differ from owner ({self.owner!r}); self-review does not count"
            )
        if self.status in {ClaimStatus.REVIEWED, ClaimStatus.ACCEPTED} and not self.reviewed_by:
            raise ValueError(f"status {self.status.value!r} requires reviewed_by")
        return self

    def with_status(self, target: ClaimStatus, **updates: object) -> AssertedCorrespondence:
        """Return a copy at ``target``, refusing transitions the lifecycle forbids.

        Returns a new object rather than mutating: claim history is auditable
        only if past states remain intact.
        """
        if not can_transition(self.status, target):
            allowed = sorted(s.value for s in CLAIM_TRANSITIONS[self.status])
            raise ValueError(
                f"cannot move claim from {self.status.value!r} to {target.value!r}; "
                f"allowed: {allowed}"
            )
        return self.model_copy(update={"status": target, **updates})


# Discriminated union: pydantic picks the class from the ``kind`` field, so
# anything deserialising stored correspondences gets the right type back and
# cannot land on a shape that is "sort of both".
Correspondence = Annotated[
    PublishedCorrespondence | AssertedCorrespondence,
    Field(discriminator="kind"),
]


class NoLink(BaseModel):
    """A recorded absence of correspondence.

    ARCHITECTURE.md: "Where no reliable link exists, the answer is 'no link' --
    recorded, not guessed." Recording absences is what makes coverage
    measurable: without them, "we found nothing" and "we never looked" produce
    identical output.
    """

    model_config = ConfigDict(frozen=True)

    from_id: str = Field(..., min_length=1)
    from_suite: SuiteName
    to_suite: SuiteName
    reason: str = Field(
        ...,
        min_length=1,
        description="Why there is no link, e.g. 'absent from esco_onet_2019'",
    )
    checked_against: str = Field(
        ...,
        min_length=1,
        description="Provenance key of the table consulted -- an absence is only "
        "meaningful relative to a specific source",
    )
