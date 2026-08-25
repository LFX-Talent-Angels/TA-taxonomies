"""Crosswalk loader: write published correspondences into Neo4j.

Entrypoint (``--mode`` is required, never defaulted)::

    python -m ta_taxonomies.crosswalks.load --mode fixture
    python -m ta_taxonomies.crosswalks.load --mode full --source esco_onet_2019
    python -m ta_taxonomies.crosswalks.load --mode full --dry-run   # report only

Pipeline is read -> parse -> resolve -> MERGE -> validate, the same shape every
suite loader uses.

Two safety properties this loader holds that a suite loader does not need:

**It never deletes anything a suite owns.** A suite loader may wipe its own
labels before a reload. This one runs *on top of* two populated suites, so a
reload removes only crosswalk-owned rows, and only those attributed to the
source key being reloaded. There is no code path here that issues a DETACH
DELETE against ``:Occupation``, ``:Skill`` or any suite label.

**It refuses to invent endpoints.** In ``--mode full`` a correspondence whose
ESCO or O*NET node is missing is reported, not created. A crosswalk that
conjures the nodes it links would manufacture occupations that no taxonomy
publishes. Fixture mode does seed small stand-in endpoints, because CI has no
loaded suites -- that path is scaffolding and says so.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from neo4j import Driver, Session

from ta_taxonomies.crosswalks.config import (
    BATCH,
    LABEL_CROSSWALK_SOURCE,
    LABEL_NO_LINK,
    REL_ATTRIBUTED_TO,
    REL_CORRESPONDS_TO,
    REL_RECORDED_NO_LINK,
)
from ta_taxonomies.crosswalks.db import neo4j_driver, verify_connectivity
from ta_taxonomies.crosswalks.esco_onet import (
    CrosswalkFormatError,
    parse_rows,
    resolve,
)
from ta_taxonomies.crosswalks.models import NoLink, Provenance, PublishedCorrespondence
from ta_taxonomies.crosswalks.schema import apply_schema
from ta_taxonomies.crosswalks.sources import get_source

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fixture.json"

# Column headers as the published ESCO/O*NET workbook writes them.
_XLSX_HEADER = "ESCO/ISCO Code"
_XLSX_COLUMNS = {
    "ESCO/ISCO Code": "esco_code",
    "ESCO/ISCO Title": "esco_title",
    "O*NET-SOC 2019 Code": "onet_code",
    "O*NET-SOC 2019 Title": "onet_title",
}


class CrosswalkLoadError(RuntimeError):
    """Raised when correspondences cannot be loaded faithfully."""


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    """Read the committed fixture: a small, hand-written, license-clean subset."""
    return json.loads((path or FIXTURE_PATH).read_text(encoding="utf-8"))


def read_xlsx_rows(path: Path) -> list[dict[str, str]]:
    """Read the published workbook, keeping every code as a string.

    ``data_only`` plus explicit string handling matters more here than it looks:
    the ESCO code column is the join key, and a reader that types '0110.10' as a
    float has already destroyed the distinction from '0110.1' before this code
    sees it. ``require_code_str`` downstream refuses numerics for that reason;
    this function simply never produces them.
    """
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise CrosswalkLoadError(
            "openpyxl is required for --mode full. Install with: pip install '.[esco-xlsx]'"
        ) from exc

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    raw = list(sheet.iter_rows(values_only=True))
    workbook.close()

    # The publisher puts two title lines and a blank above the header, so the
    # header is located rather than assumed to be row 0.
    header_index = next(
        (i for i, row in enumerate(raw) if row and row[0] == _XLSX_HEADER),
        None,
    )
    if header_index is None:
        raise CrosswalkLoadError(
            f"{path}: no header row starting with {_XLSX_HEADER!r}; file layout changed"
        )
    header = [str(cell) if cell is not None else "" for cell in raw[header_index]]
    missing = [column for column in _XLSX_COLUMNS if column not in header]
    if missing:
        raise CrosswalkLoadError(f"{path}: missing expected columns {missing}")
    positions = {_XLSX_COLUMNS[column]: header.index(column) for column in _XLSX_COLUMNS}

    rows: list[dict[str, str]] = []
    for row in raw[header_index + 1 :]:
        if not row or row[positions["esco_code"]] is None:
            continue
        rows.append({name: row[index] for name, index in positions.items()})
    return rows


# --------------------------------------------------------------------------
# Resolution against a loaded graph
# --------------------------------------------------------------------------


def _code_map(session: Session, cypher: str, *, what: str) -> dict[str, str]:
    """Build a code -> id map, refusing to proceed if a code is not unique.

    The join key for this crosswalk is the ESCO *code*, not the id. That makes
    a duplicated code the one collision this package cannot survive quietly: a
    dict comprehension keeps whichever row arrived last, the other node's
    correspondences are silently dropped, and every count still reconciles
    because nothing was ever attempted for the loser.

    The write path is already loud about duplicate *ids* -- it MATCHes without
    a label, so a second node carrying the same id makes the match count
    exceed the row count. Codes get no such protection from Cypher, because no
    constraint declares them unique, so the check has to be explicit here.
    Detection, not repair: this layer reports and stops rather than choosing a
    winner or deleting another package's node.

    Worth knowing before adding another source, because the exposure is not
    uniform. Whether a join key needs this check depends on whether the key is
    *derivable* from the id:

    - O*NET derives its id from the code (``onet:occupation:15-1252.00``), so a
      duplicated code IS a duplicated id and the suite's uniqueness constraint
      already covers it. No separate check is needed there.
    - ESCO derives its id from the concept URI (``esco:occupation:<uuid>``) and
      carries ``code`` as a *parallel* property. There is no bijection between
      them, so no constraint anywhere can make the code unique, and this check
      is the only thing standing between a collision and silent data loss.

    A future crosswalk keyed on a derivable identifier does not need this. One
    keyed on a parallel property does, and cannot borrow safety from the
    suite's schema.
    """
    mapping: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for record in session.run(cypher):
        code, node_id = record["code"], record["id"]
        existing = mapping.get(code)
        if existing is not None and existing != node_id:
            collisions.setdefault(code, [existing]).append(node_id)
            continue
        mapping[code] = node_id
    if collisions:
        sample = sorted(collisions.items())[:5]
        raise CrosswalkLoadError(
            f"{len(collisions)} {what} codes are carried by more than one node, so the "
            f"crosswalk join key is ambiguous (e.g. {sample}). Deduplicate the suite "
            "data before loading crosswalks; this layer will not pick a winner."
        )
    return mapping


def esco_code_maps(session: Session) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Read ESCO code -> id maps from the graph, plus all occupation codes.

    Read-only. The crosswalk layer looks at the suites; it does not write to
    them.
    """
    occupations = _code_map(
        session,
        "MATCH (o:Occupation) WHERE o.source = 'esco' AND o.code IS NOT NULL "
        "RETURN o.code AS code, o.id AS id",
        what="ESCO occupation",
    )
    groups = _code_map(
        session,
        "MATCH (g:ISCOGroup) WHERE g.code IS NOT NULL RETURN g.code AS code, g.id AS id",
        what="ISCO group",
    )
    return occupations, groups, sorted(occupations)


def onet_occupation_ids(session: Session) -> list[str]:
    """Read every loaded O*NET occupation id. Read-only.

    Needed so absence can be checked in both directions. Returns an empty list
    when the O*NET suite is not loaded, in which case the caller must not
    record reverse absences -- "no ESCO reaches this" and "this suite is not
    loaded" would otherwise produce identical output.
    """
    return [
        record["id"]
        for record in session.run("MATCH (o:Occupation) WHERE o.source = 'onet' RETURN o.id AS id")
    ]


def missing_targets(session: Session, ids: Iterable[str]) -> list[str]:
    """Return the ids among ``ids`` that no node in the graph carries."""
    wanted = sorted(set(ids))
    if not wanted:
        return []
    found: set[str] = set()
    for start in range(0, len(wanted), BATCH):
        chunk = wanted[start : start + BATCH]
        found.update(
            record["id"]
            for record in session.run(
                "UNWIND $ids AS id MATCH (n {id: id}) RETURN n.id AS id", ids=chunk
            )
        )
    return [node_id for node_id in wanted if node_id not in found]


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def merge_source(session: Session, provenance: Provenance) -> None:
    """MERGE the provenance node on its key, then set the citation fields."""
    session.run(
        f"""
        MERGE (s:{LABEL_CROSSWALK_SOURCE} {{key: $key}})
        SET s.title = $title,
            s.publishers = $publishers,
            s.source_url = $source_url,
            s.source_version = $source_version,
            s.retrieved_on = date($retrieved_on),
            s.license = $license,
            s.method = $method,
            s.caveats = $caveats
        """,
        key=provenance.key,
        title=provenance.title,
        publishers=list(provenance.publishers),
        source_url=provenance.source_url,
        source_version=provenance.source_version,
        retrieved_on=provenance.retrieved_on.isoformat(),
        license=provenance.license,
        method=provenance.method.value,
        caveats=list(provenance.caveats),
    )


def reset_source(session: Session, source_key: str) -> dict[str, int]:
    """Remove only what this source previously wrote. Never touches suite nodes.

    Scoped three ways on purpose: by relationship type (crosswalk-owned types
    only), by ``source_key`` (a reload of one table leaves the others intact),
    and by label for the nodes (``:NoLink``, ``:CrosswalkSource``). Reloading
    the ESCO/O*NET crosswalk cannot disturb ESCO or O*NET data even if the
    caller gets the arguments wrong.
    """
    removed_edges = session.run(
        f"""
        MATCH ()-[r:{REL_CORRESPONDS_TO}]->()
        WHERE r.source_key = $key
        WITH count(r) AS c, collect(r) AS doomed
        FOREACH (r IN doomed | DELETE r)
        RETURN c
        """,
        key=source_key,
    ).single()
    removed_no_links = session.run(
        f"""
        MATCH (n:{LABEL_NO_LINK})
        WHERE n.checked_against = $key
        WITH count(n) AS c, collect(n) AS doomed
        FOREACH (n IN doomed | DETACH DELETE n)
        RETURN c
        """,
        key=source_key,
    ).single()
    return {
        "correspondences_removed": int(removed_edges["c"]) if removed_edges else 0,
        "no_links_removed": int(removed_no_links["c"]) if removed_no_links else 0,
    }


def merge_correspondences(
    session: Session,
    correspondences: Sequence[PublishedCorrespondence],
) -> int:
    """MERGE published correspondence edges, failing if an endpoint is missing.

    MERGE identity is (from, to, source_key): the same pair asserted by two
    different published tables is two edges, because the two tables are two
    independent statements and collapsing them would erase who said what.
    """
    if not correspondences:
        return 0
    cypher = f"""
    UNWIND $rows AS row
    MATCH (a {{id: row.from_id}})
    MATCH (b {{id: row.to_id}})
    MERGE (a)-[r:{REL_CORRESPONDS_TO} {{source_key: row.source_key}}]->(b)
    SET r.strength = row.strength,
        r.from_suite = row.from_suite,
        r.to_suite = row.to_suite,
        r.source_row = row.source_row
    RETURN count(*) AS c
    """
    total = 0
    for start in range(0, len(correspondences), BATCH):
        chunk = correspondences[start : start + BATCH]
        rows = [
            {
                "from_id": item.from_id,
                "to_id": item.to_id,
                "source_key": item.provenance.key,
                "strength": item.strength.value,
                "from_suite": item.from_suite,
                "to_suite": item.to_suite,
                # Flattened to a list of "k=v" so the audit trail survives as a
                # Neo4j-native property type rather than a stringified dict.
                "source_row": [f"{k}={v}" for k, v in sorted(item.source_row.items())],
            }
            for item in chunk
        ]
        record = session.run(cypher, rows=rows).single()
        matched = int(record["c"]) if record else 0
        if matched < len(chunk):
            raise CrosswalkLoadError(
                f"{REL_CORRESPONDS_TO} merge attempted {len(chunk)} rows but matched "
                f"{matched}; {len(chunk) - matched} rows have an endpoint that does not "
                "exist in the graph. The crosswalk layer never creates endpoints."
            )
        if matched > len(chunk):
            # More matches than rows means some id is carried by more than one
            # node, so every MATCH multiplied. Distinguished from the case above
            # because the remedy is the opposite: deduplicate the suite's nodes
            # and add the uniqueness constraint its loader should have declared.
            raise CrosswalkLoadError(
                f"{REL_CORRESPONDS_TO} merge attempted {len(chunk)} rows but matched "
                f"{matched}; at least one endpoint id is carried by multiple nodes. "
                "Deduplicate the suite data before loading crosswalks."
            )
        total += matched
    return total


def merge_no_links(session: Session, no_links: Sequence[NoLink]) -> int:
    """MERGE recorded absences, each attributed to the table it was checked against."""
    if not no_links:
        return 0
    cypher = f"""
    UNWIND $rows AS row
    MATCH (a {{id: row.from_id}})
    MATCH (s:{LABEL_CROSSWALK_SOURCE} {{key: row.checked_against}})
    MERGE (n:{LABEL_NO_LINK} {{id: row.id}})
    SET n.from_id = row.from_id,
        n.from_suite = row.from_suite,
        n.to_suite = row.to_suite,
        n.reason = row.reason,
        n.checked_against = row.checked_against
    MERGE (a)-[:{REL_RECORDED_NO_LINK}]->(n)
    MERGE (n)-[:{REL_ATTRIBUTED_TO}]->(s)
    RETURN count(*) AS c
    """
    total = 0
    for start in range(0, len(no_links), BATCH):
        chunk = no_links[start : start + BATCH]
        rows = [
            {
                # Identity is (subject, target suite, table consulted): the same
                # occupation may be absent from several tables independently.
                "id": f"{item.checked_against}|{item.from_id}|{item.to_suite}",
                "from_id": item.from_id,
                "from_suite": item.from_suite,
                "to_suite": item.to_suite,
                "reason": item.reason,
                "checked_against": item.checked_against,
            }
            for item in chunk
        ]
        record = session.run(cypher, rows=rows).single()
        total += int(record["c"]) if record else 0
    return total


def seed_fixture_endpoints(session: Session, endpoints: Sequence[dict[str, Any]]) -> int:
    """Create stand-in suite nodes so fixture tests have something to link.

    CI SCAFFOLDING ONLY. Real loads run against real suites; this exists
    because the contract tests must be able to exercise the loader without a
    populated ESCO or O*NET graph. It is unreachable from ``--mode full``.

    Identity is the ``id`` alone, and labels are applied afterwards. Merging on
    ``(:Occupation:EscoNode {id})`` instead would match on the *label set*, so
    running this against a graph where a real suite had already created
    ``(:Occupation {id})`` produces a second node with the same id rather than
    reusing the first -- and every subsequent endpoint MATCH then multiplies.
    That is the "MERGE on identity, never on a compound that includes
    incidental structure" rule from ARCHITECTURE.md, and it bites here exactly
    as advertised.
    """
    written = 0
    for endpoint in endpoints:
        labels = ":".join(endpoint["labels"])
        session.run(
            "MERGE (n {id: $id}) "
            f"SET n:{labels} "
            "SET n.source = $source, n.source_id = $source_id, "
            "n.pref_label = $pref_label, n.code = $code, n.kind = $kind",
            id=endpoint["id"],
            source=endpoint["source"],
            source_id=endpoint["source_id"],
            pref_label=endpoint.get("pref_label", ""),
            code=endpoint.get("code"),
            kind=endpoint["labels"][0],
        )
        written += 1
    return written


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate(session: Session, source_key: str, expected: dict[str, int]) -> dict[str, int]:
    """Post-load assertions: counts survived, no dangling edges, every edge cited.

    Runs after every load, the same way suite loaders do. A crosswalk that
    silently loads half its rows is worse than one that fails, because the
    missing half looks like a genuine absence of correspondence.
    """
    live_edges = session.run(
        f"MATCH ()-[r:{REL_CORRESPONDS_TO}]->() WHERE r.source_key = $key RETURN count(r) AS c",
        key=source_key,
    ).single()
    live_no_links = session.run(
        f"MATCH (n:{LABEL_NO_LINK}) WHERE n.checked_against = $key RETURN count(n) AS c",
        key=source_key,
    ).single()
    uncited = session.run(
        f"MATCH ()-[r:{REL_CORRESPONDS_TO}]->() "
        f"WHERE r.source_key IS NULL OR NOT EXISTS {{ "
        f"  MATCH (s:{LABEL_CROSSWALK_SOURCE} {{key: r.source_key}}) }} "
        "RETURN count(r) AS c"
    ).single()
    within_suite = session.run(
        f"MATCH ()-[r:{REL_CORRESPONDS_TO}]->() WHERE r.from_suite = r.to_suite "
        "RETURN count(r) AS c"
    ).single()

    counts = {
        "correspondences": int(live_edges["c"]) if live_edges else 0,
        "no_links": int(live_no_links["c"]) if live_no_links else 0,
    }
    uncited_count = int(uncited["c"]) if uncited else 0
    within_suite_count = int(within_suite["c"]) if within_suite else 0

    if uncited_count:
        raise CrosswalkLoadError(
            f"{uncited_count} correspondence edges have no registered source node; "
            "every published correspondence must be citable"
        )
    if within_suite_count:
        raise CrosswalkLoadError(
            f"{within_suite_count} correspondence edges link a suite to itself; "
            "within-suite links belong in that suite's loader"
        )
    for name, want in expected.items():
        got = counts.get(name)
        if got != want:
            raise CrosswalkLoadError(f"{name}: wrote {want} but graph holds {got}")
    return counts


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _full_source_path(source_key: str) -> Path:
    """Where the pinned snapshot lives. Gitignored; fetched, never committed."""
    root = Path(os.getenv("CROSSWALK_DATA_DIR", "data/crosswalks"))
    return root / f"{source_key}.xlsx"


def build_resolution(
    session: Session,
    *,
    mode: str,
    source_key: str,
    data_path: Path | None = None,
) -> tuple[Provenance, list[PublishedCorrespondence], list[NoLink], list[str]]:
    """Read, parse and resolve, returning everything needed to write or report."""
    provenance = get_source(source_key)

    if mode == "fixture":
        document = load_fixture_document()
        raw_rows = document["rows"]
        occupations = document["esco_occupation_ids_by_code"]
        groups = document["esco_isco_ids_by_code"]
        all_codes = document.get("all_esco_occupation_codes")
        onet_ids = document.get("all_onet_occupation_ids")
    elif mode == "full":
        path = data_path or _full_source_path(source_key)
        if not path.exists():
            raise CrosswalkLoadError(
                f"pinned snapshot not found at {path}. Fetch it from "
                f"{provenance.source_url} into that path (gitignored; never commit it)."
            )
        raw_rows = read_xlsx_rows(path)
        occupations, groups, all_codes = esco_code_maps(session)
        # Empty when O*NET is not loaded yet; resolve() then skips reverse
        # absences rather than declaring all 1,016 of them unreachable.
        onet_ids = onet_occupation_ids(session) or None
        if not occupations:
            raise CrosswalkLoadError(
                "no ESCO occupations found in the graph. Load the ESCO suite first: "
                "python -m ta_taxonomies.suites.esco.load --mode full"
            )
    else:
        raise ValueError(f"unknown mode: {mode}")

    rows = parse_rows(raw_rows)
    resolution = resolve(
        rows,
        occupation_ids_by_code=occupations,
        isco_ids_by_code=groups,
        provenance=provenance,
        all_esco_occupation_codes=all_codes,
        all_onet_occupation_ids=onet_ids,
    )
    return (
        provenance,
        resolution.correspondences,
        resolution.no_links,
        resolution.unresolved_esco_codes,
    )


def load_crosswalk(
    driver: Driver,
    *,
    mode: str,
    source_key: str,
    database: str | None = None,
    data_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the whole pipeline against ``driver``. Returns a counts report.

    ``dry_run`` is strictly read-only, schema included. Creating constraints is
    a write, and the coverage report is the thing you run against someone
    else's populated graph -- it has to be safe to point at one.
    """
    if not dry_run:
        apply_schema(driver, database=database)
    with driver.session(database=database) as session:
        if mode == "fixture" and not dry_run:
            document = load_fixture_document()
            seed_fixture_endpoints(session, document.get("endpoints", []))

        provenance, correspondences, no_links, unresolved = build_resolution(
            session, mode=mode, source_key=source_key, data_path=data_path
        )

        report: dict[str, Any] = {
            "mode": mode,
            "source": provenance.key,
            "source_version": provenance.source_version,
            "method": provenance.method.value,
            "resolved_correspondences": len(correspondences),
            "recorded_no_links": len(no_links),
            "unresolved_esco_codes": len(unresolved),
            "unresolved_sample": unresolved[:10],
        }

        target_ids = {item.to_id for item in correspondences}
        absent = missing_targets(session, target_ids)
        report["onet_targets_expected"] = len(target_ids)
        report["onet_targets_missing"] = len(absent)
        report["onet_targets_missing_sample"] = absent[:10]

        if dry_run:
            report["written"] = False
            return report

        if absent:
            raise CrosswalkLoadError(
                f"{len(absent)} O*NET occupation nodes referenced by the crosswalk do not "
                f"exist in the graph (e.g. {absent[:3]}). Load the O*NET suite first, or "
                "re-run with --dry-run to see the coverage report without writing."
            )

        merge_source(session, provenance)
        report["reset"] = reset_source(session, provenance.key)
        written_edges = merge_correspondences(session, correspondences)
        written_no_links = merge_no_links(session, no_links)
        report["counts"] = validate(
            session,
            provenance.key,
            {"correspondences": written_edges, "no_links": written_no_links},
        )
        report["written"] = True
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load published crosswalks into Neo4j")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("fixture", "full"),
        help="fixture: committed license-clean subset. full: pinned snapshot on disk.",
    )
    parser.add_argument(
        "--source",
        default="esco_onet_2019",
        help="Registered provenance key (see crosswalks/sources.py)",
    )
    parser.add_argument(
        "--data-path",
        default=None,
        help="Override the pinned snapshot path for --mode full",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and report coverage without writing anything",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    with neo4j_driver() as (driver, database):
        verify_connectivity(driver)
        try:
            report = load_crosswalk(
                driver,
                mode=args.mode,
                source_key=args.source,
                database=database,
                data_path=Path(args.data_path) if args.data_path else None,
                dry_run=args.dry_run,
            )
        except (CrosswalkLoadError, CrosswalkFormatError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
            return 1
    print(json.dumps({"ok": True, **report}, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
