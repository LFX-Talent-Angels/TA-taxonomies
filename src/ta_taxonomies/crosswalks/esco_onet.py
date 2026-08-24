"""Normalize the published ESCO <-> O*NET-SOC occupation crosswalk.

Use case: turn rows of the ESCO/O*NET correspondence table into typed
``PublishedCorrespondence`` objects whose endpoints are suite-scoped ids the
loader can MERGE against, plus explicit ``NoLink`` records for ESCO
occupations the table does not mention.

Why it exists as its own module: the table is keyed by *code*, the graph is
keyed by *id*, and the translation between them is where this crosswalk can go
quietly wrong. Three specific hazards, all handled here:

1. **Codes are strings.** ESCO code ``0110.10`` (lieutenant) and ``0110.1``
   (air force officer) are different occupations. Any numeric round-trip
   collapses them. Every code path in this module refuses non-string input
   rather than coercing it.

2. **The ESCO key column mixes two levels.** 2,997 of the codes are ESCO
   occupations (they contain a dot) and 352 are ISCO group codes (they do
   not). They resolve to different labels; merging them would attach
   occupation-level links to a whole ISCO group.

3. **Absence is a result.** An ESCO occupation missing from the table gets a
   ``NoLink`` naming the table it was missing from, not silence.

Pure functions -- no Neo4j access. The caller supplies the code -> id maps.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ta_taxonomies.contract.models import SuiteName
from ta_taxonomies.crosswalks.models import (
    MatchStrength,
    NoLink,
    Provenance,
    PublishedCorrespondence,
)

# Annotated so the suite names keep their Literal type through to the models,
# which validate endpoints against exactly this set.
ESCO: SuiteName = "esco"
ONET: SuiteName = "onet"

# O*NET-SOC 2019 code: two digits, hyphen, four digits, dot, two digits.
# The trailing pair is significant -- '.00' means "same scope as the SOC
# occupation", while '.01'/'.02' are detailed O*NET occupations inside it.
_ONET_SOC = re.compile(r"^\d{2}-\d{4}\.\d{2}$")

# ESCO occupation code: dot-separated numeric groups, at least one dot,
# e.g. '2512.4', '0110.10', '2131.4.12'.
_ESCO_OCCUPATION = re.compile(r"^\d+(?:\.\d+)+$")

# ISCO group code as ESCO writes it: 1 to 4 digits, leading zeros meaningful.
_ISCO_GROUP = re.compile(r"^\d{1,4}$")

EscoSide = Literal["occupation", "isco_group"]


class CrosswalkFormatError(ValueError):
    """A row cannot be read faithfully, so it is refused rather than guessed at."""


def require_code_str(value: Any, *, field_name: str) -> str:
    """Return ``value`` as a stripped string, refusing anything already numeric.

    Deliberately strict. Spreadsheet readers hand back floats for cells that
    look numeric, and by the time a float reaches this function the leading and
    trailing zeros that distinguish '0110.10' from '110.1' are already gone --
    there is nothing left to recover. Failing loudly at the boundary is the
    only place the corruption is still visible.
    """
    if value is None:
        raise CrosswalkFormatError(f"{field_name} is empty")
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise CrosswalkFormatError(
            f"{field_name} arrived as {type(value).__name__} ({value!r}); "
            "codes must stay strings or leading and trailing zeros are already lost"
        )
    text = str(value).strip()
    if not text:
        raise CrosswalkFormatError(f"{field_name} is empty")
    return text


def classify_esco_code(code: str) -> EscoSide:
    """Decide whether an ESCO-side code names an occupation or an ISCO group."""
    if _ESCO_OCCUPATION.match(code):
        return "occupation"
    if _ISCO_GROUP.match(code):
        return "isco_group"
    raise CrosswalkFormatError(
        f"ESCO-side code {code!r} is neither an occupation code (digits with a dot) "
        "nor an ISCO group code (1-4 digits)"
    )


def onet_occupation_id(code: str) -> str:
    """Map an O*NET-SOC 2019 code to the suite-scoped id agreed with the O*NET suite.

    Shape ``onet:occupation:<code>`` matches the ``<suite>:<type>:<local>``
    convention ESCO already uses, so the type segment keeps future O*NET code
    families (elements, SOC groups) in separate namespaces.
    """
    if not _ONET_SOC.match(code):
        raise CrosswalkFormatError(
            f"O*NET-SOC code {code!r} is not of the form NN-NNNN.NN; refusing to guess"
        )
    return f"{ONET}:occupation:{code}"


@dataclass(frozen=True)
class CrosswalkRow:
    """One raw row of the correspondence table, codes still as published."""

    esco_code: str
    esco_title: str
    onet_code: str
    onet_title: str
    side: EscoSide


def parse_rows(raw: Iterable[Mapping[str, Any]]) -> list[CrosswalkRow]:
    """Read raw dict rows into typed rows, refusing anything malformed.

    Expects the column names the published file uses. Rows are validated
    individually so a bad row names itself instead of failing the whole file
    with a stack trace far from the cause.
    """
    rows: list[CrosswalkRow] = []
    for index, item in enumerate(raw):
        try:
            esco_code = require_code_str(item.get("esco_code"), field_name="esco_code")
            onet_code = require_code_str(item.get("onet_code"), field_name="onet_code")
            side = classify_esco_code(esco_code)
            onet_occupation_id(onet_code)  # validate shape now, near the offending row
        except CrosswalkFormatError as exc:
            raise CrosswalkFormatError(f"row {index}: {exc}") from exc
        rows.append(
            CrosswalkRow(
                esco_code=esco_code,
                esco_title=str(item.get("esco_title") or "").strip(),
                onet_code=onet_code,
                onet_title=str(item.get("onet_title") or "").strip(),
                side=side,
            )
        )
    return rows


@dataclass
class Resolution:
    """Outcome of resolving crosswalk rows against a loaded ESCO graph.

    Carries the misses as loudly as the hits. A crosswalk that reports only
    what it matched cannot be audited: 97% coverage and 100% coverage look
    identical unless the 3% is enumerated.
    """

    correspondences: list[PublishedCorrespondence] = field(default_factory=list)
    no_links: list[NoLink] = field(default_factory=list)
    unresolved_esco_codes: list[str] = field(default_factory=list)
    """Codes present in the table but absent from the loaded ESCO graph --
    normally an ESCO version seam between the table and the loaded release."""

    @property
    def stats(self) -> dict[str, int]:
        return {
            "correspondences": len(self.correspondences),
            "no_links": len(self.no_links),
            "unresolved_esco_codes": len(self.unresolved_esco_codes),
        }


def resolve(
    rows: Iterable[CrosswalkRow],
    *,
    occupation_ids_by_code: Mapping[str, str],
    isco_ids_by_code: Mapping[str, str],
    provenance: Provenance,
    all_esco_occupation_codes: Iterable[str] | None = None,
) -> Resolution:
    """Turn parsed rows into typed correspondences against a loaded ESCO graph.

    ``occupation_ids_by_code`` / ``isco_ids_by_code`` come from the ESCO graph
    (or a fixture). ``all_esco_occupation_codes``, when given, is the full set
    of loaded ESCO occupations -- anything in it that the table never mentions
    becomes an explicit ``NoLink``.

    Every emitted correspondence carries ``MatchStrength.UNSPECIFIED``: the
    distributed file has no strength column, and the honest record of "we were
    not told" is the unspecified value, not a plausible guess.
    """
    resolution = Resolution()
    seen_codes: set[str] = set()
    unresolved: set[str] = set()

    for row in rows:
        lookup = occupation_ids_by_code if row.side == "occupation" else isco_ids_by_code
        from_id = lookup.get(row.esco_code)
        if from_id is None:
            unresolved.add(row.esco_code)
            continue
        if row.side == "occupation":
            seen_codes.add(row.esco_code)
        resolution.correspondences.append(
            PublishedCorrespondence(
                from_id=from_id,
                to_id=onet_occupation_id(row.onet_code),
                from_suite=ESCO,
                to_suite=ONET,
                strength=MatchStrength.UNSPECIFIED,
                provenance=provenance,
                # Identifying cells only. Titles are the publishers' prose and
                # stay out of the graph (ADR-0006 decision 7, pointer not payload).
                source_row={"esco_code": row.esco_code, "onet_code": row.onet_code},
            )
        )

    if all_esco_occupation_codes is not None:
        for code in sorted(set(all_esco_occupation_codes) - seen_codes):
            node_id = occupation_ids_by_code.get(code)
            if node_id is None:
                continue
            resolution.no_links.append(
                NoLink(
                    from_id=node_id,
                    from_suite=ESCO,
                    to_suite=ONET,
                    reason=f"ESCO occupation code {code!r} does not appear in the table",
                    checked_against=provenance.key,
                )
            )

    resolution.unresolved_esco_codes = sorted(unresolved)
    return resolution
