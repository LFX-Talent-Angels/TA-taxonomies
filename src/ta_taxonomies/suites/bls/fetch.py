"""Fetch the BLS files and build the one JSON document ``load.py`` reads.

Use case: the ``fetch`` stage of the BLS pipeline::

    BLS_USER_AGENT="TalentAngels-loader/0.1 (you@example.org)" \\
        python -m ta_taxonomies.suites.bls.fetch            # download + build
    python -m ta_taxonomies.suites.bls.fetch --no-download  # build from data/bls/raw

Two steps, each usable alone:

1. ``download``: the six pinned files (see NOTES.md) into ``data/bls/raw/``,
   one per second, each checked against its SHA-256. bls.gov answers 403 to a
   request without a contact in the User-Agent, and to one that holds a URL,
   so ``BLS_USER_AGENT`` is required and checked first.
2. ``build_document``: read the spreadsheets and write
   ``data/bls/bls_document.json``. The parsing is split into small functions
   over plain rows, so tests need no spreadsheet and no network.

Reading .xlsx needs ``openpyxl`` (the ``bls-xlsx`` extra). Loading does not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ta_taxonomies.suites.bls.config import PROJECTION_CYCLE, SOC_VERSION, WAGE_PERIOD
from ta_taxonomies.suites.bls.ids import SOC_CODE, soc_level

DEFAULT_RAW_DIR = Path("data/bls/raw")
DEFAULT_OUT = Path("data/bls/bls_document.json")
TIMEOUT_S = 120
PAUSE_S = 1.0

Row = Sequence[Any]


@dataclass(frozen=True)
class SourceFile:
    name: str
    url: str
    sha256: str
    sheet: str


SOURCE_FILES: tuple[SourceFile, ...] = (
    SourceFile(
        "soc_structure_2018.xlsx",
        "https://www.bls.gov/soc/2018/soc_structure_2018.xlsx",
        "ade08af40923266f3a854842e888ca3e93c15b26a147c20a2b12a61f4c4f4077",
        "2018 Structure",
    ),
    SourceFile(
        "soc_2018_definitions.xlsx",
        "https://www.bls.gov/soc/2018/soc_2018_definitions.xlsx",
        "f8b9ca7cccea78e30abfc9b96ab8a9dd2632cc8c8b57ee9b38a9b871e8fe2a89",
        "2018 SOC",
    ),
    SourceFile(
        "soc_2018_direct_match_title_file.xlsx",
        "https://www.bls.gov/soc/2018/soc_2018_direct_match_title_file.xlsx",
        "f465e9a3de3146b6fcb7ad6e221156031dc57967c683cc5647f59d2144dbc8a5",
        "Sorted by SOC",
    ),
    SourceFile(
        "occupation.xlsx",
        "https://www.bls.gov/emp/ind-occ-matrix/occupation.xlsx",
        "08ee182f992d972593e64559ce467bad75967d2228f11ad5e2f86d4ec0ee6c00",
        "Table 1.2",
    ),
    SourceFile(
        "public-skills-data.xlsx",
        "https://www.bls.gov/emp/skills/public-skills-data.xlsx",
        "3e95a389425e6280a9d2152f3a2b8191317465785e75a2ffa75b554eda8ceb73",
        "EP Skills Data",
    ),
    SourceFile(
        "ep_skill_onet_element_crosswalk.xlsx",
        "https://www.bls.gov/emp/skills/ep_skill_onet_element_crosswalk.xlsx",
        "9e29498cb9d0cb5574331ae5616dd17da6fca96d18e0aef9ee16ead4361ad16a",
        "Crosswalk",
    ),
)


class BlsFetchError(RuntimeError):
    """A download or a source file is not what this loader was built for."""


# -- download ------------------------------------------------------------------

_CONTACT = re.compile(r"[^\s@()]+@[^\s@()]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)


def check_user_agent(user_agent: str | None) -> str:
    """The User-Agent to send, or ``BlsFetchError`` if bls.gov would refuse it."""
    ua = (user_agent or "").strip()
    if not ua:
        raise BlsFetchError(
            "BLS_USER_AGENT is required: bls.gov refuses anonymous downloads. "
            "Set e.g. BLS_USER_AGENT='TalentAngels-loader/0.1 (you@example.org)'."
        )
    if _URL.search(ua):
        raise BlsFetchError("BLS_USER_AGENT must not contain a URL; bls.gov refuses those.")
    if not _CONTACT.search(ua):
        raise BlsFetchError("BLS_USER_AGENT must name a contact e-mail address.")
    return ua


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _http_get(url: str, user_agent: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        data: bytes = response.read()
        return data


def download(
    raw_dir: Path,
    user_agent: str | None,
    *,
    allow_new_release: bool = False,
    get: Callable[[str, str], bytes] = _http_get,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    """Download every source file that is missing or stale; return name → SHA-256.

    A file whose hash differs from the pinned one is a new BLS release. That
    stops the run unless ``allow_new_release``: a new release must be read,
    checked and re-pinned in NOTES.md and here, not loaded by surprise.
    """
    ua = check_user_agent(user_agent)
    raw_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    first = True
    for source in SOURCE_FILES:
        path = raw_dir / source.name
        if path.exists() and sha256_of(path) == source.sha256:
            hashes[source.name] = source.sha256
            continue
        if not first:
            pause(PAUSE_S)
        first = False
        data = get(source.url, ua)
        if data[:2] != b"PK":  # every .xlsx is a zip; an HTML error page is not
            raise BlsFetchError(f"{source.url} did not return a spreadsheet (blocked?)")
        digest = hashlib.sha256(data).hexdigest()
        if digest != source.sha256 and not allow_new_release:
            raise BlsFetchError(
                f"{source.name} changed (sha256 {digest}); BLS published a new release. "
                "Check it, then re-run with --allow-new-release and update the pins."
            )
        path.write_bytes(data)
        hashes[source.name] = digest
    return hashes


# -- reading -------------------------------------------------------------------

_MISSING = {"", "—", "-", "–", "*", "#", "n/a", "N/A"}
_HREF = re.compile(r'href="([^"]+)"')


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    """A numeric cell as float; BLS's "—" (not available) and blanks are None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value).replace(",", "")
    if text in _MISSING:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _category(value: Any) -> str | None:
    text = _text(value)
    return None if text in _MISSING else text


def _rows_after_header(rows: Iterable[Row], first_header: str) -> Iterator[Row]:
    """The rows after the header row whose first cell is ``first_header``.

    BLS sheets open with a title block of varying length, so the header is
    found by its text rather than by its position.
    """
    found = False
    for row in rows:
        if not found:
            if row and _text(row[0]) == first_header:
                found = True
            continue
        yield row
    if not found:
        raise BlsFetchError(f"header {first_header!r} not found; did the file layout change?")


def parse_structure(rows: Iterable[Row]) -> list[dict[str, str]]:
    """SOC 2018 structure → groups and detailed occupations, in file order.

    Each row puts its code in the column of its level (major, minor, broad,
    detailed) and its title in the fifth column. ``parent`` is the group one
    level up, taken from the file's order: the digits do not always say it
    (broad group 29-1140 sits in minor group 29-1000, not "29-1100").
    """
    levels = ("major", "minor", "broad", "detailed")
    out: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for row in _rows_after_header(rows, "Major Group"):
        cells = [_text(c) for c in (list(row) + [None] * 5)[:5]]
        title = cells[4]
        for index, level in enumerate(levels):
            code = cells[index]
            if code and SOC_CODE.fullmatch(code):
                if soc_level(code) != level:
                    raise BlsFetchError(f"SOC {code} sits in the {level} column")
                entry = {"code": code, "title": title, "level": level}
                if index:
                    parent = current.get(levels[index - 1])
                    if parent is None or parent[:2] != code[:2]:
                        raise BlsFetchError(f"SOC {code} has no {levels[index - 1]} group above it")
                    entry["parent"] = parent
                current[level] = code
                for lower in levels[index + 1 :]:
                    current.pop(lower, None)
                out.append(entry)
                break
    return out


def parse_definitions(rows: Iterable[Row]) -> dict[str, str]:
    """SOC code → definition (detailed occupations only; groups have none)."""
    out: dict[str, str] = {}
    for row in _rows_after_header(rows, "SOC Group"):
        cells = (list(row) + [None] * 4)[:4]
        code, definition = _text(cells[1]), _text(cells[3])
        if SOC_CODE.fullmatch(code) and definition:
            out[code] = definition
    return out


def parse_direct_match_titles(rows: Iterable[Row]) -> dict[str, list[str]]:
    """SOC code → its direct-match titles ("CEO", "Web Developer"), de-duplicated."""
    out: dict[str, list[str]] = {}
    for row in _rows_after_header(rows, "2018 SOC Code"):
        cells = (list(row) + [None] * 3)[:3]
        code, title = _text(cells[0]), _text(cells[2])
        if SOC_CODE.fullmatch(code) and title:
            titles = out.setdefault(code, [])
            if title not in titles:
                titles.append(title)
    return out


def parse_projections(rows: Iterable[Row]) -> dict[str, dict[str, Any]]:
    """EP table 1.2 line items → NEM code → projection facts.

    Columns (2025-35 layout): title, code, type, employment 2025, 2035,
    distribution 2025, 2035, change numeric, change percent, percent
    self-employed, annual openings, median annual wage, entry education, work
    experience, on-the-job training, OOH title, OOH link.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in _rows_after_header(rows, "2025 National Employment Matrix title"):
        cells = (list(row) + [None] * 17)[:17]
        if _text(cells[2]) != "Line item":
            continue
        code = _text(cells[1])
        if not SOC_CODE.fullmatch(code):
            continue
        link = _HREF.search(_text(cells[16]))
        out[code] = {
            "code": code,
            "title": _text(cells[0]),
            # Employment is published in thousands of jobs.
            "employment_2025": _thousands(cells[3]),
            "employment_2035": _thousands(cells[4]),
            "employment_change": _thousands(cells[7]),
            "employment_change_pct": _number(cells[8]),
            "openings_annual": _thousands(cells[10]),
            "median_wage_annual": _number(cells[11]),
            "entry_education": _category(cells[12]),
            "work_experience": _category(cells[13]),
            "job_training": _category(cells[14]),
            "ooh_url": link.group(1) if link else None,
        }
    return out


def _thousands(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else round(number * 1000)


def parse_skill_scores(
    rows: Iterable[Row],
) -> tuple[dict[str, dict[str, float]], dict[str, list[str]]]:
    """EP skills data → (NEM code → skill name → score, NEM code → O*NET-SOC codes).

    The file has one row per O*NET element; the skill score repeats on each,
    so the first one seen is kept.
    """
    scores: dict[str, dict[str, float]] = {}
    onet: dict[str, list[str]] = {}
    for row in _rows_after_header(rows, "2025 National Employment Matrix title"):
        cells = (list(row) + [None] * 13)[:13]
        code, skill = _text(cells[1]), _text(cells[7])
        score = _number(cells[8])
        if not SOC_CODE.fullmatch(code) or not skill or score is None:
            continue
        scores.setdefault(code, {}).setdefault(skill, score)
        onet_code = _text(cells[9])
        if onet_code:
            codes = onet.setdefault(code, [])
            if onet_code not in codes:
                codes.append(onet_code)
    return scores, onet


def parse_skill_crosswalk(rows: Iterable[Row]) -> list[dict[str, Any]]:
    """The 17 EP skills with their definition and O*NET element pointers."""
    skills: dict[str, dict[str, Any]] = {}
    for row in _rows_after_header(rows, "EP skills ID"):
        cells = (list(row) + [None] * 6)[:6]
        name = _text(cells[1])
        if not name:
            continue
        skill = skills.setdefault(
            name,
            {
                "ep_id": _text(cells[0]),
                "name": name,
                "definition": _text(cells[2]),
                "onet_elements": [],
            },
        )
        element = _text(cells[3])
        if element and element not in skill["onet_elements"]:
            skill["onet_elements"].append(element)
    return sorted(skills.values(), key=lambda s: int(s["ep_id"] or 0))


# -- assembling ----------------------------------------------------------------

#: OEWS/EP codes that combine detailed SOC occupations from more than one broad
#: group, or only some of one, so the code's digits cannot name its members.
#: Members per the EP titles (e.g. 21-1018 "Substance abuse, behavioral
#: disorder, and mental health counselors" = 21-1011 + 21-1014). ``assemble``
#: checks every member is a detailed SOC occupation without its own EP row.
COMBINED_CODES: dict[str, tuple[str, ...]] = {
    "21-1018": ("21-1011", "21-1014"),
    "25-2052": ("25-2055", "25-2056"),
    "25-9045": ("25-9042", "25-9043", "25-9049"),
    "51-2028": ("51-2022", "51-2023"),
    "53-1047": ("53-1042", "53-1043", "53-1044", "53-1049"),
}


def _projection_code(code: str, broad: str, projections: dict[str, dict[str, Any]]) -> str:
    """The EP code whose figures cover detailed SOC ``code`` (in broad group ``broad``)."""
    if code in projections:
        return code
    for combined, members in COMBINED_CODES.items():
        if code in members and combined in projections:
            return combined
    return broad


def assemble(
    *,
    structure: list[dict[str, str]],
    definitions: dict[str, str],
    titles: dict[str, list[str]],
    projections: dict[str, dict[str, Any]],
    scores: dict[str, dict[str, float]],
    onet_codes: dict[str, list[str]],
    skills: list[dict[str, Any]],
    files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One document: groups, occupations (with their EP facts) and skills.

    EP publishes a few occupations only as one aggregate under their broad
    group code ("31-1120"). A detailed occupation without its own EP row takes
    its broad group's row, flagged ``aggregate: true``.
    """
    groups = [entry for entry in structure if entry["level"] != "detailed"]
    detailed = {entry["code"] for entry in structure if entry["level"] == "detailed"}
    for combined, members in COMBINED_CODES.items():
        bad = [m for m in members if m not in detailed or m in projections]
        if combined in projections and bad:
            raise BlsFetchError(f"combined code {combined}: members {bad} are not as expected")
    occupations: list[dict[str, Any]] = []
    used_projection_codes: set[str] = set()
    for entry in structure:
        if entry["level"] != "detailed":
            continue
        code = entry["code"]
        projection_code = _projection_code(code, entry["parent"], projections)
        projection = projections.get(projection_code)
        if projection is not None:
            used_projection_codes.add(projection_code)
            projection = {
                **{k: v for k, v in projection.items() if k != "title"},
                "aggregate": projection_code != code,
            }
        own_titles = [t for t in titles.get(code, []) if t != entry["title"]]
        occupations.append(
            {
                "code": code,
                "group": entry["parent"],
                "title": entry["title"],
                "definition": definitions.get(code, ""),
                "alt_titles": own_titles,
                "projection": projection,
                "skills": [
                    {"name": name, "score": score}
                    for name, score in sorted(scores.get(projection_code, {}).items())
                ],
                "onet_soc_codes": list(onet_codes.get(projection_code, [])),
            }
        )
    return {
        "meta": {
            "soc_version": SOC_VERSION,
            "projection_cycle": PROJECTION_CYCLE,
            "wage_period": WAGE_PERIOD,
            "files": dict(files or {}),
            "unmatched_projection_codes": sorted(set(projections) - used_projection_codes),
        },
        "groups": groups,
        "occupations": occupations,
        "skills": skills,
    }


def _sheet_rows(path: Path, sheet: str) -> Iterator[Row]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise BlsFetchError(
            'openpyxl is required to read the BLS spreadsheets: uv pip install -e ".[bls-xlsx]"'
        ) from exc
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        yield from workbook[sheet].iter_rows(values_only=True)
    finally:
        workbook.close()


def build_document(raw_dir: Path) -> dict[str, Any]:
    """Read the six source files in ``raw_dir`` into one document."""
    by_name = {source.name: source for source in SOURCE_FILES}
    missing = [name for name in by_name if not (raw_dir / name).exists()]
    if missing:
        raise BlsFetchError(
            f"missing in {raw_dir}: {', '.join(missing)} (run without --no-download)"
        )

    def rows(name: str) -> Iterator[Row]:
        return _sheet_rows(raw_dir / name, by_name[name].sheet)

    scores, onet_codes = parse_skill_scores(rows("public-skills-data.xlsx"))
    return assemble(
        structure=parse_structure(rows("soc_structure_2018.xlsx")),
        definitions=parse_definitions(rows("soc_2018_definitions.xlsx")),
        titles=parse_direct_match_titles(rows("soc_2018_direct_match_title_file.xlsx")),
        projections=parse_projections(rows("occupation.xlsx")),
        scores=scores,
        onet_codes=onet_codes,
        skills=parse_skill_crosswalk(rows("ep_skill_onet_element_crosswalk.xlsx")),
        files={name: sha256_of(raw_dir / name) for name in by_name},
    )


def summary(doc: dict[str, Any]) -> dict[str, Any]:
    occupations = doc["occupations"]
    return {
        "groups": len(doc["groups"]),
        "occupations": len(occupations),
        "with_projection": sum(1 for o in occupations if o["projection"]),
        "with_aggregate_projection": sum(
            1 for o in occupations if o["projection"] and o["projection"]["aggregate"]
        ),
        "with_skills": sum(1 for o in occupations if o["skills"]),
        "alt_titles": sum(len(o["alt_titles"]) for o in occupations),
        "skills": len(doc["skills"]),
        "unmatched_projection_codes": doc["meta"]["unmatched_projection_codes"],
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch BLS files and build the BLS document")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--no-download", action="store_true", help="Build from files already in --raw-dir"
    )
    parser.add_argument(
        "--allow-new-release",
        action="store_true",
        help="Accept files whose SHA-256 differs from the pinned release",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if not args.no_download:
            download(
                args.raw_dir,
                os.getenv("BLS_USER_AGENT"),
                allow_new_release=args.allow_new_release,
            )
        doc = build_document(args.raw_dir)
    except (BlsFetchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out), **summary(doc)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
