"""The typed distinction between published data and project claims must hold.

These tests are the enforcement mechanism for ADR-0006 decision 4. Each one
pins a property that, if it broke, would let a project assertion be read as
source data somewhere downstream.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from ta_taxonomies.crosswalks.models import (
    CLAIM_TRANSITIONS,
    AssertedCorrespondence,
    ClaimStatus,
    MappingMethod,
    MatchStrength,
    NoLink,
    Provenance,
    PublishedCorrespondence,
    can_transition,
)

PROV = Provenance(
    key="test_source",
    title="Test crosswalk",
    publishers=("Someone Official",),
    source_url="https://example.org/table.xlsx",
    source_version="v1",
    retrieved_on=date(2026, 8, 24),
    license="CC BY 4.0",
    method=MappingMethod.EXPERT_MAPPING,
)


def _published(**overrides: object) -> PublishedCorrespondence:
    payload: dict[str, object] = {
        "from_id": "esco:occupation:abc",
        "to_id": "onet:occupation:15-1252.00",
        "from_suite": "esco",
        "to_suite": "onet",
        "provenance": PROV,
    }
    payload.update(overrides)
    return PublishedCorrespondence(**payload)  # type: ignore[arg-type]


def _asserted(**overrides: object) -> AssertedCorrespondence:
    payload: dict[str, object] = {
        "from_id": "esco:skill:abc",
        "to_id": "onet:element:2.B.1.a",
        "from_suite": "esco",
        "to_suite": "onet",
        "owner": "mentee",
        "rationale": "definitions overlap almost verbatim",
    }
    payload.update(overrides)
    return AssertedCorrespondence(**payload)  # type: ignore[arg-type]


class TestPublishedRequiresProvenance:
    def test_provenance_is_mandatory(self) -> None:
        """A published correspondence with no citation must be unconstructable."""
        with pytest.raises(ValidationError):
            PublishedCorrespondence(  # type: ignore[call-arg]
                from_id="esco:occupation:abc",
                to_id="onet:occupation:15-1252.00",
                from_suite="esco",
                to_suite="onet",
            )

    def test_kind_discriminator_is_fixed(self) -> None:
        assert _published().kind == "published"
        assert _asserted().kind == "asserted"

    def test_strength_defaults_to_unspecified_not_exact(self) -> None:
        """Silence about strength must never be optimism about strength."""
        assert _published().strength is MatchStrength.UNSPECIFIED

    def test_unpinned_version_is_refused(self) -> None:
        """ADR-0006 d.6: pinned snapshots, not a live moving target."""
        with pytest.raises(ValidationError, match="not pinned"):
            PROV.model_copy(update={"source_version": "latest"}).model_validate(
                {**PROV.model_dump(), "source_version": "latest"}
            )


class TestAssertedRequiresAccountability:
    @pytest.mark.parametrize("missing", ["owner", "rationale"])
    def test_owner_and_rationale_are_mandatory(self, missing: str) -> None:
        payload = {
            "from_id": "esco:skill:abc",
            "to_id": "onet:element:2.B.1.a",
            "from_suite": "esco",
            "to_suite": "onet",
            "owner": "mentee",
            "rationale": "because",
        }
        del payload[missing]
        with pytest.raises(ValidationError):
            AssertedCorrespondence(**payload)  # type: ignore[arg-type]

    def test_new_claim_starts_at_observed(self) -> None:
        assert _asserted().status is ClaimStatus.OBSERVED

    def test_self_review_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="self-review"):
            _asserted(status=ClaimStatus.REVIEWED, reviewed_by="mentee")

    @pytest.mark.parametrize("status", [ClaimStatus.REVIEWED, ClaimStatus.ACCEPTED])
    def test_review_states_require_a_reviewer(self, status: ClaimStatus) -> None:
        with pytest.raises(ValidationError, match="requires reviewed_by"):
            _asserted(status=status)


class TestClaimLifecycle:
    def test_accepted_is_unreachable_without_passing_review(self) -> None:
        """The point of the lifecycle: no claim becomes project opinion alone."""
        for origin, targets in CLAIM_TRANSITIONS.items():
            if ClaimStatus.ACCEPTED in targets:
                assert origin is ClaimStatus.REVIEWED, (
                    f"{origin.value} can reach accepted without review"
                )

    def test_happy_path(self) -> None:
        claim = _asserted()
        claim = claim.with_status(ClaimStatus.PROPOSED)
        claim = claim.with_status(ClaimStatus.REVIEWED, reviewed_by="mentor")
        claim = claim.with_status(ClaimStatus.ACCEPTED)
        assert claim.status is ClaimStatus.ACCEPTED
        assert claim.reviewed_by == "mentor"

    def test_illegal_transition_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot move claim"):
            _asserted().with_status(ClaimStatus.ACCEPTED)

    def test_every_live_state_can_be_deprecated(self) -> None:
        for origin in ClaimStatus:
            if origin is ClaimStatus.DEPRECATED:
                continue
            assert can_transition(origin, ClaimStatus.DEPRECATED)

    def test_revived_claim_reenters_at_proposed_not_accepted(self) -> None:
        """A withdrawn claim re-argues its case; it does not resume its old trust."""
        assert CLAIM_TRANSITIONS[ClaimStatus.DEPRECATED] == frozenset({ClaimStatus.PROPOSED})

    def test_transition_returns_a_copy(self) -> None:
        claim = _asserted()
        moved = claim.with_status(ClaimStatus.PROPOSED)
        assert claim.status is ClaimStatus.OBSERVED
        assert moved is not claim


class TestEndpointRules:
    def test_within_suite_link_is_refused(self) -> None:
        """A link inside one suite is that suite's business, not a crosswalk."""
        with pytest.raises(ValidationError, match="within-suite"):
            _published(to_id="esco:occupation:def", to_suite="esco")

    def test_unscoped_id_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not scoped to suite"):
            _published(to_id="15-1252.00")

    def test_identical_endpoints_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            _published(from_id="onet:occupation:15-1252.00")


class TestNoLink:
    def test_absence_names_the_table_it_was_checked_against(self) -> None:
        """An absence with no denominator cannot be distinguished from not looking."""
        with pytest.raises(ValidationError):
            NoLink(  # type: ignore[call-arg]
                from_id="esco:occupation:abc",
                from_suite="esco",
                to_suite="onet",
                reason="absent",
            )
