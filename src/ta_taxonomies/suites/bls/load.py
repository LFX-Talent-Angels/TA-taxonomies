"""BLS/SOC graph loader: build the suite knowledge graph in Neo4j.

Use case: reproducible ingestion for CI (small committed fixture) or the full
Employment Projections + OES occupation reference on a developer machine.
Pipeline is read → normalize → MERGE → validate. Querying is tools.py, after
the graph exists.

Entrypoint::

    python -m ta_taxonomies.suites.bls.fetch --out data/bls/raw
    python -m ta_taxonomies.suites.bls.load --mode fixture
    python -m ta_taxonomies.suites.bls.load --mode full --data-dir data/bls/raw

Three things this loader does that the ESCO and O*NET ones do not, each because
BLS's shape forces it:

* **It materialises SOC groups BLS never published a line for.** 276 of the 450
  broad groups implied by BLS's own detailed codes have no row in any file. The
  spine still needs them to be roll-up targets, so they are created from the
  code with ``title_source='derived'`` and no title, and validation counts them
  rather than letting a labelless node look like a load bug.
* **It checks what the aspect codes mean.** ``ep/`` ships no lookup file for
  ``aspect_type``, so the meanings this suite assigns are re-derived on every
  load from arithmetic the source has to satisfy — see
  ``verify_aspect_semantics``. A code list that changes under us stops the load
  instead of silently relabelling 796,810 numbers.
* **It asserts the absence of a skills layer.** ADR-0006 adopts BLS as the
  occupation spine, and the graph is checked for having no ``HAS_SKILL`` edge
  out of a BLS node. An invariant nobody enforces is a comment.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from neo4j import Driver, Session

from ta_taxonomies.suites.bls.config import (
    AGGREGATE_IND_CODES,
    ASPECT_CHANGE_NUMERIC,
    ASPECT_CHANGE_PERCENT,
    ASPECT_IND_SHARE_BASE,
    ASPECT_IND_SHARE_PROJ,
    ASPECT_MEDIAN_WAGE,
    ASPECT_OCC_SHARE_BASE,
    ASPECT_OCC_SHARE_PROJ,
    ASPECT_OPENINGS,
    ASPECT_PCT_SELF_EMPLOYED,
    ASPECT_PROJECTED,
    BASE_YEAR,
    CANONICAL_LABELS,
    CLASS_OF_WORKER_CODES,
    EMPLOYMENT_UNIT,
    IND_TOTAL_ALL,
    LABEL_BLS_NODE,
    LABEL_INDUSTRY,
    LABEL_OCCUPATION,
    LABEL_SOC_GROUP,
    OCCUPATION_ONLY_ASPECTS,
    PROJECTION_YEAR,
    REL_BROADER_THAN,
    REL_EMPLOYED_IN,
    RELEASE,
    SHARE_MAX,
    SHARE_MIN,
    SOC_LEVEL_MAJOR,
    SOC_LEVEL_MINOR,
    SOC_TAXONOMY,
    SOURCE,
    TOTAL_ALL_OCCUPATIONS,
    WAGE_UNIT,
    WAGE_YEAR,
)
from ta_taxonomies.suites.bls.db import neo4j_config_from_env, neo4j_driver, verify_connectivity
from ta_taxonomies.suites.bls.ids import (
    dedupe_titles,
    industry_id,
    is_detailed_occupation,
    nearest_present_parent,
    normalize_industry_code,
    normalize_soc_code,
    soc_ancestors,
    soc_level,
    soc_node_id,
    soc_parent_chain,
)
from ta_taxonomies.suites.bls.schema import apply_schema

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fixture.json"
BATCH = 500

# BLS's null in these text columns. Not a value, not an empty string.
NA = "-"

# Employment is published in thousands to one decimal, so a rounded figure
# carries ±0.05. A1 is the difference of two such figures plus its own
# rounding: three half-units.
ROUNDING = 0.05
A1_TOLERANCE = 3 * ROUNDING

# Below this base-year employment the percent-change check stops testing
# semantics and starts testing rounding. BLS computes A2 from unrounded
# internals, so for a cell of 1,200 jobs a published A1 of "0.0" sits beside a
# published A2 of "4.7" and both are correct. At 5.0 thousand and above, the
# identity holds on all 28,207 qualifying cells of the 2024–34 round.
A2_MIN_BASE = 5.0


class BlsLoadValidationError(RuntimeError):
    """Raised when normalized BLS rows cannot be represented faithfully."""


# --- source reading --------------------------------------------------------


def read_tsv(path: Path) -> list[dict[str, str]]:
    """Read one tab-delimited BLS flat file.

    ``quoting=csv.QUOTE_NONE``: SOC definitions and industry titles contain
    inch marks and apostrophes, and the default dialect would treat a stray
    double quote as the start of a quoted field and swallow the rest of the row.

    Headers and values are stripped because the flat files pad ``series_id`` to
    a fixed width — ``'EPU151252TE1000               '`` is the same series as
    the unpadded form, and a join on the padded string silently matches nothing.
    """
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        return [
            {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            for row in reader
        ]


def _text(row: Mapping[str, Any], key: str) -> str:
    value = str(row.get(key) or "").strip()
    return "" if value == NA else value


def _float(row: Mapping[str, Any], key: str) -> float | None:
    value = _text(row, key)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int(row: Mapping[str, Any], key: str) -> int | None:
    value = _text(row, key)
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


# --- release and aspect checks --------------------------------------------


def check_release(dates_rows: Iterable[Mapping[str, Any]]) -> None:
    """Fail if the files are not the projection round this suite is written for.

    ``config.BASE_YEAR`` / ``PROJECTION_YEAR`` / ``WAGE_YEAR`` are stamped onto
    every node, and the scoring window in ``GROWTH_WINDOW_PCT`` was chosen
    against this round's published range. Loading a later round under the older
    labels would put the wrong years on every node and leave no trace.
    """
    seen: dict[str, tuple[int | None, int | None]] = {}
    for row in dates_rows:
        source = _text(row, "data_source")
        seen[source] = (_int(row, "base_year"), _int(row, "proj_year"))

    projections = seen.get("optd") or seen.get("io_matrix")
    if projections is None:
        raise BlsLoadValidationError(
            f"ep.dates names no projection round (rows: {sorted(seen)}); "
            "this loader cannot confirm which release it is reading"
        )
    base, projected = projections
    if (base, projected) != (BASE_YEAR, PROJECTION_YEAR):
        raise BlsLoadValidationError(
            f"ep.dates says {base}–{projected}, this suite is written for "
            f"{BASE_YEAR}–{PROJECTION_YEAR}. Bump RELEASE/BASE_YEAR/"
            "PROJECTION_YEAR in config.py and re-check GROWTH_WINDOW_PCT "
            "against the new round before loading."
        )
    wages = seen.get("wages")
    if wages is not None and wages[0] != WAGE_YEAR:
        raise BlsLoadValidationError(
            f"ep.dates says wages are {wages[0]}, this suite is written for {WAGE_YEAR}"
        )


def verify_aspect_semantics(
    base_by_series: Mapping[str, float],
    aspects: Mapping[str, Mapping[str, float]],
    total_series: set[str],
) -> dict[str, int]:
    """Re-derive what the ``aspect_type`` codes mean, and fail if they moved.

    ``ep/`` publishes a lookup file for every other coded column — eductrn,
    otjt, wkex, footnote — and **none for aspect_type**. So the meanings in
    config.py were established from the data rather than read off a code list,
    and re-deriving them on every load is what keeps them from becoming folklore.

    Two identities BLS's own numbers have to satisfy if the codes mean what
    this suite says they do:

    * ``A1 == PR − base``. Exact to the published rounding, on every cell.
    * ``A2 == A1 / base × 100``, on cells large enough for the identity to be
      about semantics rather than rounding (see ``A2_MIN_BASE``).

    Plus one structural claim: the occupation-level aspects (self-employed
    share, openings, median wage) appear **only** on total-all-industries
    series. If BLS started publishing a median wage per industry cell, this
    suite's node properties would silently become one arbitrary cell's number.

    Returns the counts it checked, so a caller can print evidence that the
    check had something to check rather than passing vacuously.
    """
    checked_a1 = checked_a2 = 0
    for series_id, values in aspects.items():
        base = base_by_series.get(series_id)
        projected = values.get(ASPECT_PROJECTED)
        change = values.get(ASPECT_CHANGE_NUMERIC)
        if base is None or projected is None or change is None:
            continue
        checked_a1 += 1
        if abs((projected - base) - change) > A1_TOLERANCE:
            raise BlsLoadValidationError(
                f"{series_id}: {ASPECT_CHANGE_NUMERIC} is {change} but "
                f"{ASPECT_PROJECTED} − base is {projected - base:.1f}. "
                f"The aspect codes no longer mean what config.py says they do."
            )
        percent = values.get(ASPECT_CHANGE_PERCENT)
        if percent is None or base < A2_MIN_BASE:
            continue
        checked_a2 += 1
        # Error propagation through BLS's own rounding of base and change.
        tolerance = 100 * (ROUNDING / base + abs(change) * ROUNDING / (base * base)) + ROUNDING
        if abs(change / base * 100 - percent) > tolerance:
            raise BlsLoadValidationError(
                f"{series_id}: {ASPECT_CHANGE_PERCENT} is {percent} but "
                f"{ASPECT_CHANGE_NUMERIC}/base is {change / base * 100:.2f}. "
                f"The aspect codes no longer mean what config.py says they do."
            )

    misplaced = sorted(
        series_id
        for series_id, values in aspects.items()
        if series_id not in total_series and OCCUPATION_ONLY_ASPECTS & set(values)
    )
    if misplaced:
        raise BlsLoadValidationError(
            f"{len(misplaced)} series carry an occupation-level aspect "
            f"({sorted(OCCUPATION_ONLY_ASPECTS)}) outside {IND_TOTAL_ALL}, "
            f"first {misplaced[0]}; this suite reads them as the occupation's "
            "own figures and would now be reading one industry cell's instead"
        )
    return {"a1_identity": checked_a1, "a2_identity": checked_a2}


# --- normalization ---------------------------------------------------------


def _node_row(
    *,
    node_id: str,
    label_kind: str,
    source_id: str,
    pref_label: str,
    alt_labels: Iterable[str] = (),
    description: str = "",
    code: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "source": SOURCE,
        "source_id": source_id,
        "pref_label": pref_label or "",
        "alt_labels": list(alt_labels),
        "description": description or "",
        "code": code,
        "kind": label_kind,
        "release": RELEASE,
        "extra": dict(extra or {}),
    }


def _lookup(rows: Iterable[Mapping[str, Any]], key: str, value: str) -> dict[str, str]:
    """Build a code → text map from one of the small ep lookup files."""
    out: dict[str, str] = {}
    for row in rows:
        code = _text(row, key)
        if code:
            out[code] = _text(row, value)
    return out


def _series_index(
    doc: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, float], dict[str, dict[str, float]]]:
    """Index the three EP tables by series id, refusing an ambiguous key.

    Every one of these is a *key that travels parallel to the id* in the sense
    of ``suites/onet/NOTES.md``: nothing in the graph schema constrains it, so a
    repeat would resolve last-wins and be indistinguishable from the earlier row
    never having existed. The 2024–34 round has none — 113,473 series ids over
    113,473 rows, and 796,810 (series, aspect) pairs over 796,810 rows — which
    is exactly why the guards are worth having. They exist for the round that
    changes shape.
    """
    series: dict[str, dict[str, Any]] = {}
    for row in doc.get("ep_series", []):
        series_id = _text(row, "series_id")
        if series_id in series:
            raise BlsLoadValidationError(f"duplicate series id in ep.series: {series_id}")
        series[series_id] = dict(row)

    base: dict[str, float] = {}
    for row in doc.get("ep_data", []):
        series_id = _text(row, "series_id")
        value = _float(row, "value")
        if series_id in base and base[series_id] != value:
            raise BlsLoadValidationError(
                f"conflicting employment values for {series_id}: {base[series_id]} then {value}"
            )
        if value is not None:
            base[series_id] = value

    aspects: dict[str, dict[str, float]] = {}
    for row in doc.get("ep_aspect", []):
        series_id = _text(row, "series_id")
        aspect = _text(row, "aspect_type")
        value = _float(row, "value")
        if value is None:
            continue
        bucket = aspects.setdefault(series_id, {})
        if aspect in bucket and bucket[aspect] != value:
            raise BlsLoadValidationError(
                f"conflicting {aspect} for {series_id}: {bucket[aspect]} then {value}"
            )
        bucket[aspect] = value
    return series, base, aspects


def _check_shares(series_id: str, values: Mapping[str, float]) -> None:
    """A share outside [0, 100] means the file changed shape, not that it moved."""
    for aspect in (
        ASPECT_IND_SHARE_BASE,
        ASPECT_IND_SHARE_PROJ,
        ASPECT_OCC_SHARE_BASE,
        ASPECT_OCC_SHARE_PROJ,
        ASPECT_PCT_SELF_EMPLOYED,
    ):
        value = values.get(aspect)
        if value is not None and not (SHARE_MIN <= value <= SHARE_MAX):
            raise BlsLoadValidationError(
                f"{series_id}: {aspect} is {value}, outside the "
                f"{SHARE_MIN}–{SHARE_MAX} percent range it is declared on"
            )


def normalize_document(doc: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Translate a BLS document (fixture JSON or full-file dict) into MERGE payloads."""
    check_release(doc.get("ep_dates", []))

    education = _lookup(doc.get("ep_eductrn", []), "eductrn_code", "eductrn_desc")
    training = _lookup(doc.get("ep_otjt", []), "otjt_code", "otjt_text")
    experience = _lookup(doc.get("ep_wkex", []), "wkex_code", "wkex_text")

    # --- titles, from the two publishers, with a stated precedence ---------
    # ep.occupation is the Employment Projections program's own title and is
    # what every projection figure is published under, so it wins. The OES name
    # differs from it on 3 of the 1,093 codes both files carry (and by casing on
    # 1,005 more); where it differs it is kept as an alias rather than dropped,
    # because someone who typed the OES form typed a real BLS title.
    ep_titles: dict[str, str] = {}
    display_levels: dict[str, int | None] = {}
    for row in doc.get("ep_occupation", []):
        code = normalize_soc_code(row["occ_code"])
        ep_titles[code] = _text(row, "occ_title")
        display_levels[code] = _int(row, "display_level")

    oe_titles: dict[str, str] = {}
    definitions: dict[str, str] = {}
    for row in doc.get("oe_occupation", []):
        code = normalize_soc_code(row["occupation_code"])
        oe_titles[code] = _text(row, "occupation_name")
        description = _text(row, "occupation_description")
        if description:
            definitions[code] = description

    lay_titles: dict[str, list[str]] = {}
    for row in doc.get("ep_laytitle", []):
        code = normalize_soc_code(row["oes_code"])
        title = _text(row, "lay_title")
        if title:
            lay_titles.setdefault(code, []).append(title)

    series, base_by_series, aspects = _series_index(doc)
    total_series = {
        series_id for series_id, row in series.items() if _text(row, "ind_code") == IND_TOTAL_ALL
    }
    checks = verify_aspect_semantics(base_by_series, aspects, total_series)

    # --- the spine: published codes plus every ancestor they imply ---------
    published = (
        set(ep_titles)
        | set(oe_titles)
        | {normalize_soc_code(row["occ_code"]) for row in series.values()}
    )
    # The minor groups BLS actually publishes. Built before anything else that
    # walks the hierarchy, because the minor level is a lookup rather than a
    # derivation — ``ids.soc_minor_candidates`` argues why at length. Getting
    # this from the *published* set rather than from the codes is the whole
    # difference between the SOC tree and a plausible-looking wrong one.
    minor_groups = {code for code in published if soc_level(code) == SOC_LEVEL_MINOR}

    derived: set[str] = set()
    for code in published:
        for ancestor in soc_parent_chain(code, minor_groups):
            if ancestor not in published:
                derived.add(ancestor)
    spine = published | derived
    # Deriving a group cannot invent a minor group — resolve_soc_minor only
    # returns codes that are already published — so the set is stable and does
    # not need a second pass. Asserted rather than assumed.
    if {code for code in derived if soc_level(code) == SOC_LEVEL_MINOR}:
        raise BlsLoadValidationError(
            "the derived-ancestor pass produced a minor group. Minor groups are "
            "looked up in the published set, never derived, so this means "
            "resolve_soc_minor returned a code BLS does not publish"
        )

    # Per-occupation figures, read from the total-all-industries series.
    occupation_facts: dict[str, dict[str, Any]] = {}
    class_of_worker: dict[str, dict[str, Any]] = {}
    for series_id, row in series.items():
        code = normalize_soc_code(row["occ_code"])
        ind_code = _text(row, "ind_code")
        values = aspects.get(series_id, {})
        _check_shares(series_id, values)
        if ind_code == IND_TOTAL_ALL:
            occupation_facts[code] = {
                "employment_base": base_by_series.get(series_id),
                "employment_projected": values.get(ASPECT_PROJECTED),
                "employment_change": values.get(ASPECT_CHANGE_NUMERIC),
                "employment_change_percent": values.get(ASPECT_CHANGE_PERCENT),
                "openings_annual_average": values.get(ASPECT_OPENINGS),
                "median_annual_wage": values.get(ASPECT_MEDIAN_WAGE),
                "percent_self_employed": values.get(ASPECT_PCT_SELF_EMPLOYED),
                "typical_education": education.get(_text(row, "eductrn_code")) or None,
                "on_the_job_training": training.get(_text(row, "otjt_code")) or None,
                "work_experience": experience.get(_text(row, "wkex_code")) or None,
            }
        elif ind_code in CLASS_OF_WORKER_CODES:
            # TE1100 / TE1200 are class-of-worker splits, not industries.
            # Putting "Self-employed workers" beside "Crop production" in an
            # answer to "which industries employ X" would be wrong in kind, so
            # they land on the occupation as their own properties instead.
            prefix = CLASS_OF_WORKER_CODES[ind_code]
            bucket = class_of_worker.setdefault(code, {})
            bucket[f"employment_{prefix}_base"] = base_by_series.get(series_id)
            bucket[f"employment_{prefix}_projected"] = values.get(ASPECT_PROJECTED)

    occupations: list[dict[str, Any]] = []
    soc_groups: list[dict[str, Any]] = []
    for code in sorted(spine):
        level = soc_level(code)
        ancestors = soc_ancestors(code, minor_groups)
        title = ep_titles.get(code) or oe_titles.get(code) or ""
        title_source = (
            "ep.occupation"
            if ep_titles.get(code)
            else "oe.occupation"
            if oe_titles.get(code)
            else "derived"
        )
        aliases = dedupe_titles(lay_titles.get(code, []))
        other_title = oe_titles.get(code)
        if other_title and other_title != title:
            aliases = dedupe_titles([*aliases, other_title])
        aliases = [alias for alias in aliases if alias != title]

        extra: dict[str, Any] = {
            "soc_level": level,
            "soc_major": ancestors.get("major"),
            "soc_minor": ancestors.get("minor"),
            "soc_broad": ancestors.get("broad"),
            "taxonomy": SOC_TAXONOMY,
            "title_source": title_source,
            "nem_display_level": display_levels.get(code),
            "alt_label_count": len(aliases),
            "base_year": BASE_YEAR,
            "projection_year": PROJECTION_YEAR,
            "wage_year": WAGE_YEAR,
            "employment_unit": EMPLOYMENT_UNIT,
            "wage_unit": WAGE_UNIT,
        }
        extra.update(occupation_facts.get(code, {}))
        extra.update(class_of_worker.get(code, {}))

        row = _node_row(
            node_id=soc_node_id(code),
            label_kind=(LABEL_OCCUPATION if is_detailed_occupation(code) else LABEL_SOC_GROUP),
            source_id=code,
            pref_label=title,
            alt_labels=aliases,
            description=definitions.get(code, ""),
            code=code,
            extra=extra,
        )
        if is_detailed_occupation(code):
            occupations.append(row)
        else:
            soc_groups.append(row)

    # --- BROADER_THAN: to the nearest ancestor that is actually a node -----
    broader: list[dict[str, Any]] = []
    for code in sorted(spine):
        if code == TOTAL_ALL_OCCUPATIONS or soc_level(code) == SOC_LEVEL_MAJOR:
            # Major groups are the roots of the SOC tree, and 00-0000 is BLS's
            # economy-wide total rather than a group that contains them. Making
            # it their parent would put every occupation under a node whose
            # employment figure is the sum of all the others.
            continue
        parent = nearest_present_parent(code, spine, minor_groups)
        if parent is None:
            raise BlsLoadValidationError(
                f"{code} has no ancestor in the spine; soc_ancestors and the "
                "derived-group pass disagree, which cannot happen unless one of "
                "them changed"
            )
        chain = soc_parent_chain(code, minor_groups)
        broader.append(
            {
                "from_id": soc_node_id(code),
                "to_id": soc_node_id(parent),
                # How many SOC levels this edge jumps. 1 is the normal case; 2
                # means BLS publishes no line for the level in between, which is
                # common for broad groups and is a fact about the source, not a
                # modelling choice. Roll-up does not depend on it — soc_broad /
                # soc_minor / soc_major on the node are always complete.
                "levels_skipped": chain.index(parent),
                "parent_level": soc_level(parent),
            }
        )

    # --- industries and the employment matrix ------------------------------
    industries: list[dict[str, Any]] = []
    industry_codes: set[str] = set()
    for row in doc.get("ep_industry", []):
        code = normalize_industry_code(row["ind_code"])
        if code in AGGREGATE_IND_CODES:
            continue
        industry_codes.add(code)
        industries.append(
            _node_row(
                node_id=industry_id(code),
                label_kind=LABEL_INDUSTRY,
                source_id=code,
                pref_label=_text(row, "ind_title"),
                code=code,
                extra={
                    "nem_display_level": _int(row, "display_level"),
                    "base_year": BASE_YEAR,
                    "projection_year": PROJECTION_YEAR,
                    "employment_unit": EMPLOYMENT_UNIT,
                },
            )
        )

    employed_in: list[dict[str, Any]] = []
    for series_id, row in series.items():
        ind_code = _text(row, "ind_code")
        if ind_code in AGGREGATE_IND_CODES:
            continue
        if ind_code not in industry_codes:
            # A fixture holds a slice, so a cell may name an industry the
            # document does not carry. Dropping the edge is correct; keeping it
            # would fail the endpoint check with a less useful message.
            continue
        code = normalize_soc_code(row["occ_code"])
        if code not in spine:
            continue
        values = aspects.get(series_id, {})
        employed_in.append(
            {
                "from_id": soc_node_id(code),
                "to_id": industry_id(ind_code),
                "series_id": series_id,
                "employment_base": base_by_series.get(series_id),
                "employment_projected": values.get(ASPECT_PROJECTED),
                "employment_change": values.get(ASPECT_CHANGE_NUMERIC),
                "employment_change_percent": values.get(ASPECT_CHANGE_PERCENT),
                "industry_share_of_occupation_base": values.get(ASPECT_IND_SHARE_BASE),
                "industry_share_of_occupation_projected": values.get(ASPECT_IND_SHARE_PROJ),
                "occupation_share_of_industry_base": values.get(ASPECT_OCC_SHARE_BASE),
                "occupation_share_of_industry_projected": values.get(ASPECT_OCC_SHARE_PROJ),
                # L = a detail line, S = a summary that contains other lines.
                # Summary and detail cells coexist by design in the National
                # Employment Matrix, so summing employment across every edge
                # double-counts. These two properties are how a caller picks a
                # non-overlapping slice; nothing here can pick one for them.
                "occupation_type": _text(row, "occ_type"),
                "industry_type": _text(row, "ind_type"),
            }
        )

    return {
        "occupations": occupations,
        "soc_groups": soc_groups,
        "industries": industries,
        "broader_than": broader,
        "employed_in": employed_in,
        "_checks": [
            {
                "spine_published": len(published),
                "spine_derived": len(derived),
                **checks,
            }
        ],
    }


# --- merge -----------------------------------------------------------------


def _merge_nodes(session: Session, label: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    canonical = CANONICAL_LABELS[label]
    # MERGE on the umbrella label, then apply the kind and canonical labels.
    #
    # MERGE matches the *whole* pattern, labels included. Merging on the kind
    # label means a node already holding this id under a different label set is
    # invisible to it and gets duplicated instead of matched — and the load
    # then validates clean, because the uniqueness constraint and every count
    # query are label-scoped too. Measured in suites/onet/NOTES.md; not
    # rediscovered here. The umbrella is the one label every node of this suite
    # carries and the one its constraint is on, which makes it both the correct
    # identity and an indexed lookup.
    #
    # It is still not proof against a node that lacks the umbrella entirely —
    # nothing indexed can be — so validate_load checks for that separately.
    extra_labels = f", n:{canonical}" if canonical else ""
    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{LABEL_BLS_NODE} {{id: row.id}})
    SET n:{label}{extra_labels}
    SET n.source = row.source,
        n.source_id = row.source_id,
        n.pref_label = row.pref_label,
        n.alt_labels = row.alt_labels,
        n.description = row.description,
        n.code = row.code,
        n.kind = row.kind,
        n.release = row.release
    SET n += row.extra
    """
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        session.run(cypher, rows=chunk)
        total += len(chunk)
    return total


def _merge_rel_count(
    session: Session,
    cypher: str,
    rows: list[dict[str, Any]],
    *,
    relationship: str,
) -> int:
    """Run batched MERGE, failing clearly when any endpoint is missing."""
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        rec = session.run(cypher, rows=chunk).single()
        matched = int(rec["c"]) if rec else 0
        if matched != len(chunk):
            raise BlsLoadValidationError(
                f"{relationship} merge attempted {len(chunk)} rows but matched {matched}; "
                f"missing endpoints {len(chunk) - matched}"
            )
        total += matched
    return total


# Endpoints of BROADER_THAN may be an occupation or a group, so both sides
# match the umbrella label — which is also the one the constraint indexes.
_BROADER_THAN_CYPHER = f"""
UNWIND $rows AS row
MATCH (a:{LABEL_BLS_NODE} {{id: row.from_id}})
MATCH (b:{LABEL_BLS_NODE} {{id: row.to_id}})
MERGE (a)-[r:{REL_BROADER_THAN}]->(b)
SET r.levels_skipped = row.levels_skipped,
    r.parent_level = row.parent_level
RETURN count(*) AS c
"""

# Properties are set from an explicit map rather than `r += row` so the join
# keys never leak onto the relationship and the edge's shape is readable here.
_EMPLOYED_IN_CYPHER = f"""
UNWIND $rows AS row
MATCH (o:{LABEL_BLS_NODE} {{id: row.from_id}})
MATCH (i:{LABEL_INDUSTRY} {{id: row.to_id}})
MERGE (o)-[r:{REL_EMPLOYED_IN}]->(i)
SET r.series_id = row.series_id,
    r.employment_base = row.employment_base,
    r.employment_projected = row.employment_projected,
    r.employment_change = row.employment_change,
    r.employment_change_percent = row.employment_change_percent,
    r.industry_share_of_occupation_base = row.industry_share_of_occupation_base,
    r.industry_share_of_occupation_projected = row.industry_share_of_occupation_projected,
    r.occupation_share_of_industry_base = row.occupation_share_of_industry_base,
    r.occupation_share_of_industry_projected = row.occupation_share_of_industry_projected,
    r.occupation_type = row.occupation_type,
    r.industry_type = row.industry_type
RETURN count(*) AS c
"""


def wipe_bls_graph(session: Session) -> None:
    """Delete this suite's nodes only.

    Scoped by ``source`` **and** by the suite's umbrella label, because the
    canonical labels (``:Occupation``, ``:SOCGroup``) are shared with every
    other suite in the same graph.
    """
    session.run(
        f"""
        MATCH (n:{LABEL_BLS_NODE})
        WHERE n.source = $source
        DETACH DELETE n
        """,
        source=SOURCE,
    )


def load_normalized(
    driver: Driver,
    payload: dict[str, list[dict[str, Any]]],
    *,
    database: str | None = None,
    wipe: bool = True,
) -> dict[str, int]:
    apply_schema(driver, database=database)
    with driver.session(database=database) as session:
        if wipe:
            wipe_bls_graph(session)

        counts: dict[str, int] = {}
        for key, label in (
            ("soc_groups", LABEL_SOC_GROUP),
            ("occupations", LABEL_OCCUPATION),
            ("industries", LABEL_INDUSTRY),
        ):
            print(f"  merging {key} …", flush=True)
            counts[key] = _merge_nodes(session, label, payload[key])
            print(f"    → {counts[key]:,}", flush=True)

        for key, cypher, rel in (
            ("broader_than", _BROADER_THAN_CYPHER, REL_BROADER_THAN),
            ("employed_in", _EMPLOYED_IN_CYPHER, REL_EMPLOYED_IN),
        ):
            print(f"  merging {rel} …", flush=True)
            counts[key] = _merge_rel_count(session, cypher, payload[key], relationship=rel)
            print(f"    → {counts[key]:,}", flush=True)

    return counts


def validate_load(
    driver: Driver,
    expected: Mapping[str, int],
    *,
    database: str | None = None,
) -> dict[str, int]:
    """Assert load invariants; return live counts.

    The invariants are BLS's. Two of them would be wrong in another suite:

    * **Not every node has a title.** 276 broad groups exist only because a
      detailed code implies them, and BLS publishes no line for them. Demanding
      a preferred label everywhere would refuse to load correct data; instead
      the labelless nodes are counted and required to be exactly the derived
      ones.
    * **There are no skill edges, and that is checked.** ADR-0006 adopts BLS as
      the occupation spine precisely because it has no skills layer, so a
      ``HAS_SKILL`` edge out of a BLS node means someone mixed sources.
    """
    with driver.session(database=database) as session:

        def count_label(label: str) -> int:
            rec = session.run(
                f"MATCH (n:{label}) WHERE n.source = $source RETURN count(n) AS c",
                source=SOURCE,
            ).single()
            return int(rec["c"]) if rec else 0

        def count_rel(rel: str) -> int:
            rec = session.run(
                f"MATCH (a:{LABEL_BLS_NODE})-[r:{rel}]->(b:{LABEL_BLS_NODE}) "
                "WHERE a.source = $source AND b.source = $source RETURN count(r) AS c",
                source=SOURCE,
            ).single()
            return int(rec["c"]) if rec else 0

        live = {
            "occupations": count_label(LABEL_OCCUPATION),
            "soc_groups": count_label(LABEL_SOC_GROUP),
            "industries": count_label(LABEL_INDUSTRY),
            "broader_than": count_rel(REL_BROADER_THAN),
            "employed_in": count_rel(REL_EMPLOYED_IN),
        }

        def scalar(cypher: str) -> int:
            rec = session.run(cypher, source=SOURCE).single()
            return int(rec["c"]) if rec else 0

        blank = scalar(
            f"""
            MATCH (n:{LABEL_BLS_NODE})
            WHERE n.id IS NULL OR n.id = '' OR n.source IS NULL
              OR n.source_id IS NULL OR n.source_id = ''
            RETURN count(n) AS c
            """
        )
        # A node holding one of this suite's ids without the suite's umbrella
        # label. The uniqueness constraint cannot see it — constraints are per
        # label — so MERGE creates a second node and every count still adds up.
        # Left undetected, two nodes share one id and the identity rule the
        # whole graph rests on is quietly false.
        #
        # This is not hypothetical, and it is the exact reason this suite was
        # tested against a graph that already held another one: a crosswalk that
        # materialises an endpoint before its suite is loaded leaves such a
        # node, and the wipe does not remove it, because deleting another
        # package's node is not this loader's call. Failing here names it.
        impostors = scalar(
            f"""
            MATCH (n)
            WHERE n.id STARTS WITH '{SOURCE}:' AND NOT n:{LABEL_BLS_NODE}
            RETURN count(n) AS c
            """
        )
        dangling = scalar(
            f"""
            MATCH (:{LABEL_BLS_NODE} {{source: $source}})-[r:{REL_EMPLOYED_IN}]->(x)
            WHERE NOT x:{LABEL_INDUSTRY}
            RETURN count(r) AS c
            """
        )
        self_parent = scalar(
            f"""
            MATCH (n:{LABEL_BLS_NODE} {{source: $source}})-[r:{REL_BROADER_THAN}]->(n)
            RETURN count(r) AS c
            """
        )
        # Every node that is not a major group must reach one. This is the
        # assertion that the spine is a spine: an unrooted node is a code whose
        # ancestors were never materialised, and it would be invisible to every
        # roll-up that walks edges.
        unrooted = scalar(
            f"""
            MATCH (n:{LABEL_BLS_NODE} {{source: $source}})
            WHERE (n:{LABEL_OCCUPATION} OR n:{LABEL_SOC_GROUP})
              AND n.soc_level <> '{SOC_LEVEL_MAJOR}'
              AND NOT (n)-[:{REL_BROADER_THAN}]->(:{LABEL_BLS_NODE})
            RETURN count(n) AS c
            """
        )
        # Every BROADER_THAN edge must go to the *immediate* SOC level above.
        #
        # This is 0 on all 1,380 edges of a full load, and that is not an
        # accident worth shrugging at — it is the check that the derived-group
        # pass ran. ``nearest_present_parent`` is written to skip a level BLS
        # publishes no line for, and materialising those groups is what makes
        # the skip unreachable. If derivation ever silently drops a group, the
        # loader will not fail on a missing endpoint (the edge simply lands
        # further up) and every count will still add up. This is the only place
        # that would notice.
        skipping = scalar(
            f"""
            MATCH (:{LABEL_BLS_NODE} {{source: $source}})
                  -[r:{REL_BROADER_THAN}]->(:{LABEL_BLS_NODE})
            WHERE coalesce(r.levels_skipped, 0) > 0
            RETURN count(r) AS c
            """
        )
        # A derived group has no title by construction; anything else without
        # one lost its title somewhere between the file and the graph.
        untitled_not_derived = scalar(
            f"""
            MATCH (n:{LABEL_BLS_NODE} {{source: $source}})
            WHERE (n.pref_label IS NULL OR n.pref_label = '')
              AND n.title_source <> 'derived'
            RETURN count(n) AS c
            """
        )
        # ADR-0006 adopts this suite for having no skills layer. Asserted here
        # rather than only written down, because an invariant nobody enforces is
        # a comment.
        skill_edges = scalar(
            f"""
            MATCH (:{LABEL_BLS_NODE} {{source: $source}})-[r:HAS_SKILL]->()
            RETURN count(r) AS c
            """
        )
        derived_groups = scalar(
            f"""
            MATCH (n:{LABEL_BLS_NODE} {{source: $source}})
            WHERE n.title_source = 'derived'
            RETURN count(n) AS c
            """
        )

    for name, expected_count in expected.items():
        actual = live[name]
        if actual != expected_count:
            raise BlsLoadValidationError(
                f"BLS {name} count mismatch: expected {expected_count}, found {actual}"
            )
    if blank:
        raise BlsLoadValidationError(f"blank identity nodes: {blank}")
    if impostors:
        raise BlsLoadValidationError(
            f"{impostors} node(s) carry a '{SOURCE}:' id without the "
            f":{LABEL_BLS_NODE} label, so they duplicate this suite's identities; "
            "label them or remove them before loading"
        )
    if dangling:
        raise BlsLoadValidationError(
            f"{REL_EMPLOYED_IN} edges not ending on an industry: {dangling}"
        )
    if self_parent:
        raise BlsLoadValidationError(f"{REL_BROADER_THAN} self-loops: {self_parent}")
    if unrooted:
        raise BlsLoadValidationError(
            f"{unrooted} SOC node(s) with no parent and no major-group level; "
            "the spine is not connected"
        )
    if skipping:
        raise BlsLoadValidationError(
            f"{skipping} {REL_BROADER_THAN} edge(s) skip a SOC level. Every "
            "ancestor a code implies is materialised, so a skip means the "
            "derived-group pass dropped a group and the tree is no longer the "
            "SOC hierarchy"
        )
    if untitled_not_derived:
        raise BlsLoadValidationError(
            f"{untitled_not_derived} node(s) have no pref_label but are not "
            "derived groups; a published title went missing"
        )
    if skill_edges:
        raise BlsLoadValidationError(
            f"{skill_edges} HAS_SKILL edge(s) out of BLS nodes. BLS publishes no "
            "skills layer (ADR-0006); these came from somewhere else and this "
            "suite must not appear to own them"
        )
    live["derived_groups"] = derived_groups
    return live


# --- documents -------------------------------------------------------------


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURE_PATH).read_text(encoding="utf-8"))


# document key → (subdirectory, filename). Everything this suite reads, in one
# place, so the fixture builder and the full loader cannot drift apart.
SOURCE_FILES: dict[str, tuple[str, str]] = {
    "ep_dates": ("ep", "ep.dates"),
    "ep_occupation": ("ep", "ep.occupation"),
    "ep_industry": ("ep", "ep.industry"),
    "ep_laytitle": ("ep", "ep.laytitle"),
    "ep_series": ("ep", "ep.series"),
    "ep_data": ("ep", "ep.data.1.AllData"),
    "ep_aspect": ("ep", "ep.aspect"),
    "ep_eductrn": ("ep", "ep.eductrn"),
    "ep_otjt": ("ep", "ep.otjt"),
    "ep_wkex": ("ep", "ep.wkex"),
    "oe_occupation": ("oe", "oe.occupation"),
}


def load_full_document(data_dir: Path) -> dict[str, Any]:
    """Build a fixture-shaped document from the downloaded BLS flat files.

    ``data_dir`` is what ``fetch.py`` wrote — a directory holding ``ep/`` and
    ``oe/``.
    """
    doc: dict[str, Any] = {
        "meta": {
            "suite": SOURCE,
            "mode": "full",
            "release": RELEASE,
            "data_dir": str(data_dir),
        }
    }
    for key, (subdir, filename) in SOURCE_FILES.items():
        path = data_dir / subdir / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Missing BLS file: {path}. Run "
                "`python -m ta_taxonomies.suites.bls.fetch --out "
                f"{data_dir}` first."
            )
        print(f"  reading {subdir}/{filename} …", flush=True)
        doc[key] = read_tsv(path)
        print(f"    → {len(doc[key]):,} rows", flush=True)
    return doc


def _dedupe_edges(rows: list[dict[str, Any]], *, label: str = "") -> list[dict[str, Any]]:
    """Keep one row per (from_id, to_id); last row wins for properties.

    Deduplication is legitimate — MERGE would collapse the pair anyway, and the
    expected counts have to match what MERGE produces. Doing it *silently* is
    not: a source that started publishing two rows per pair would lose one set
    of properties with every count still adding up. So the count is printed.
    The 2024–34 round drops nothing.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        seen[(row["from_id"], row["to_id"])] = row
    dropped = len(rows) - len(seen)
    if dropped and label:
        print(f"  note: {label} collapsed {dropped:,} duplicate endpoint pairs", flush=True)
    return list(seen.values())


def run_load(
    mode: str = "fixture",
    *,
    data_dir: Path | None = None,
    fixture_path: Path | None = None,
    wipe: bool = True,
) -> dict[str, int]:
    """Load BLS/SOC into Neo4j and return live node/relationship counts.

    mode:
        ``fixture`` — committed subset (tests/CI).
        ``full`` — downloaded flat files under ``data_dir`` or ``BLS_DATA_DIR``.
    wipe:
        If True (default), detach-delete existing BLS nodes before load.
    """
    if mode == "fixture":
        doc = load_fixture_document(fixture_path)
    elif mode == "full":
        root = data_dir or Path(os.getenv("BLS_DATA_DIR", "data/bls/raw"))
        doc = load_full_document(root)
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(f"Normalizing ({mode}) …", flush=True)
    payload = normalize_document(doc)
    checks = payload.pop("_checks")[0]

    for key in ("occupations", "soc_groups", "industries"):
        unique: dict[str, dict[str, Any]] = {}
        for row in payload[key]:
            unique[row["id"]] = row
        payload[key] = list(unique.values())
    for key in ("broader_than", "employed_in"):
        payload[key] = _dedupe_edges(payload[key], label=key)

    print(
        "  unique "
        + " ".join(f"{k}={len(payload[k]):,}" for k in sorted(payload))
        + f"\n  spine: {checks['spine_published']:,} published codes "
        f"+ {checks['spine_derived']:,} derived groups"
        f"\n  aspect identities re-derived: A1 on {checks['a1_identity']:,} series, "
        f"A2 on {checks['a2_identity']:,}",
        flush=True,
    )

    cfg = neo4j_config_from_env()
    # Printed before anything is deleted: a load starts by wiping this suite's
    # nodes, and the URI is the one thing worth being sure about first.
    print(f"Target: {cfg['uri']} database={cfg['database']} (wipe={wipe})", flush=True)

    with neo4j_driver() as (driver, database):
        verify_connectivity(driver)
        counts = load_normalized(driver, payload, database=database, wipe=wipe)
        expected = {
            "occupations": counts["occupations"],
            "soc_groups": counts["soc_groups"],
            "industries": counts["industries"],
            "broader_than": len(payload["broader_than"]),
            "employed_in": len(payload["employed_in"]),
        }
        print("Validating …", flush=True)
        live = validate_load(driver, expected, database=database)
    return live


__all__ = [
    "BlsLoadValidationError",
    "check_release",
    "load_full_document",
    "load_fixture_document",
    "load_normalized",
    "normalize_document",
    "read_tsv",
    "run_load",
    "validate_load",
    "verify_aspect_semantics",
    "wipe_bls_graph",
]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the BLS/SOC suite into Neo4j")
    parser.add_argument(
        "--mode",
        choices=("fixture", "full"),
        required=True,
        help=(
            "fixture = committed subset; full = downloaded BLS flat files. "
            "Required on purpose: a load wipes this suite's nodes, so the target "
            "must be a choice rather than a default."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override BLS_DATA_DIR for --mode full",
    )
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Do not DETACH DELETE existing BLS nodes before load",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    live = run_load(mode=args.mode, data_dir=args.data_dir, wipe=not args.no_wipe)
    print(json.dumps({"ok": True, "mode": args.mode, "release": RELEASE, "counts": live}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
