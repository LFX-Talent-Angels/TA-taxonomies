"""Pinned provenance for every correspondence table this repo transcribes.

Use case: look up a ``Provenance`` by key when normalizing rows or when
citing an edge back to a caller.

Why it exists: ADR-0006 decision 6 requires pinned, versioned snapshots with
retrieval dates -- access terms move, and a crosswalk that says only "from
O*NET" is not reproducible. Registering sources here rather than inlining
strings at each call site also makes the registry itself reviewable: adding a
source is a visible diff in one file, which is where a code owner would want
to see it.

Nothing here holds licensed payload. These are pointers and dates.
"""

from __future__ import annotations

from datetime import date

from ta_taxonomies.crosswalks.models import MappingMethod, Provenance

# The ESCO <-> O*NET-SOC occupation crosswalk, published jointly by the
# European Commission (DG EMPL) and the US Department of Labor's O*NET.
#
# Read the caveats before trusting a single row. This table is *published* --
# two public authorities put their names on it -- but it is not *deterministic*.
# Its own technical report describes a fine-tuned BERT model proposing
# candidates that human validators then accepted or rejected. That is a
# perfectly respectable way to build a crosswalk, and it is emphatically not
# the same epistemic status as "the SOC code is the prefix before the dot".
# ``MappingMethod`` exists to keep those two apart.
ESCO_ONET_2019 = Provenance(
    key="esco_onet_2019",
    title="ESCO to O*NET-SOC 2019 crosswalk",
    publishers=(
        "European Commission, DG Employment, Social Affairs and Inclusion (ESCO)",
        "U.S. Department of Labor / ETA, O*NET Resource Center",
    ),
    source_url="https://www.onetcenter.org/crosswalks/esco/ESCO_to_ONET-SOC.xlsx",
    source_version="O*NET-SOC 2019",
    retrieved_on=date(2026, 8, 24),
    license=(
        "O*NET distribution under CC BY 4.0; ESCO reusable with attribution. "
        "The file itself is a pinned snapshot and is never committed."
    ),
    method=MappingMethod.MODEL_ASSISTED_VALIDATED,
    caveats=(
        "Published methodology defines exact/narrower/broader/close/related match "
        "types, but the distributed XLSX has no strength column. Rows therefore load "
        "as MatchStrength.UNSPECIFIED; inferring a strength would be invention.",
        "ESCO documents a second version of this crosswalk that adds 'related' "
        "matches and states it did not go through quality assurance or the US DOL "
        "validation process. That version is deliberately not registered here.",
        "Built with a fine-tuned BERT model proposing candidates for human "
        "validation, not by deterministic derivation. Best reported model placed "
        "the correct ESCO concept first for 85% of exact matches.",
        "Many-to-many. Measured on the 2026-08-24 snapshot: 8,627 rows, 3,349 "
        "distinct ESCO-side codes, 958 distinct O*NET-SOC codes, mean 2.58 O*NET "
        "per ESCO code, and only 40.9% of matched ESCO codes map to exactly one "
        "O*NET occupation. Treating a row as an identity is wrong.",
        "The ESCO-side key column mixes two levels: 2,997 ESCO occupation codes "
        "(which contain a dot) and 352 ISCO group codes (which do not). They "
        "resolve to different node labels and must not be merged.",
        "Only 958 of the 1,016 O*NET-SOC 2019 occupations appear at all, so some "
        "O*NET occupations are unreachable from ESCO through this table.",
        "The ESCO side is keyed by code, not URI. Codes are strings: '0110.10' "
        "and '0110.1' are different occupations, and numeric parsing destroys the "
        "distinction.",
    ),
)


# Keyed registry. Adding an entry is the reviewable act; normalizers look
# sources up by key so a provenance can never be constructed ad hoc at a call
# site and drift from the registered one.
SOURCES: dict[str, Provenance] = {
    ESCO_ONET_2019.key: ESCO_ONET_2019,
}


def get_source(key: str) -> Provenance:
    """Return the registered provenance for ``key``.

    Raises instead of returning ``None`` so an unregistered source cannot
    quietly become an edge with no citation.
    """
    try:
        return SOURCES[key]
    except KeyError:
        known = ", ".join(sorted(SOURCES)) or "(none)"
        raise KeyError(f"unknown crosswalk source {key!r}; registered: {known}") from None
