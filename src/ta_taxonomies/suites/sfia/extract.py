"""SFIA extraction: published pages → the factual layer, and nothing else.

Use case: turn a local SFIA snapshot (see ``fetch.py``) or a licensee's own CSV
export into the structure-only document ``load.py`` normalizes — skill codes,
skill names, level numbers, the published related-skill links, and the category
tree when the input carries one.

**This module is where the licence is enforced, so read this before editing it.**

SFIA's published pages contain a great deal that this repository may not store:
the skill description, the "essence of the level", the definition of each skill
at each level, and the guidance notes. A public Apache-2.0 repository is
redistribution to everyone, and redistributing SFIA material needs a fee-bearing
licence (TA-workspace ADR-0006 §2).

The parsers below therefore **read** that text and **drop** it on purpose, at
the point of extraction, with the reason attached at each site. What survives is
the fact that a level section had content — a boolean, "this skill is defined at
level 4" — never the content itself. Two independent guards make an accident
here fail loudly rather than ship quietly:

* ``ALLOWED_NODE_KEYS`` — a node row may carry no other key, so a description
  cannot be threaded through under a new name.
* ``LICENSED_FIELD_MARKERS`` and ``MAX_LABEL_CHARS`` — no key whose name stems
  from licensed prose, and no string longer than a label plausibly is.

Both run in ``assert_factual_only``, which ``load.py`` calls before the first
MERGE. Adding SFIA prose to the graph is therefore not a thing that can be done
absent-mindedly: it requires editing the allowlist in ``config.py``, and this
docstring is what a reviewer will be pointed at when they ask why.
"""

from __future__ import annotations

import csv
import html as html_module
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ta_taxonomies.suites.sfia.config import (
    ALLOWED_NODE_KEYS,
    LICENSED_FIELD_MARKERS,
    MAX_LABEL_CHARS,
    SNAPSHOT_INDEX_FILE,
    SNAPSHOT_SKILLS_DIR,
    SNAPSHOT_STRUCTURE_CSV,
    VERSION,
)
from ta_taxonomies.suites.sfia.ids import SfiaIdError, normalize_level, normalize_skill_code


class SfiaLicenseError(RuntimeError):
    """Raised when a row would put licensed SFIA text into the graph."""


class SfiaExtractError(RuntimeError):
    """Raised when a snapshot page does not have the documented shape."""


# --- licence guard ---------------------------------------------------------


def assert_factual_only(rows: Iterable[Mapping[str, Any]], *, what: str) -> None:
    """Refuse any row that could carry licensed SFIA text.

    Three checks, deliberately overlapping, because each catches what the others
    cannot:

    * an **allowlist** of top-level keys — a new key cannot appear by accident;
    * a **name check** over every key, including inside ``extra``, so
      ``level_description`` or ``essenceOfTheLevel`` is refused by its stem even
      if someone adds it to the allowlist;
    * a **length ceiling** on every stored string, because the first two check
      where text was put and not what it is. The longest SFIA 9 skill name is 44
      characters; a sentence of SFIA prose is not.
    """
    for row in rows:
        for key in row:
            if key not in ALLOWED_NODE_KEYS:
                raise SfiaLicenseError(
                    f"{what}: key {key!r} is not in ALLOWED_NODE_KEYS. "
                    "This suite may store only SFIA's codes, names, level numbers and "
                    "structure (ADR-0006 §2); adding a field is a deliberate act, not "
                    "a default."
                )
        _assert_no_licensed_values(row, what=what, path="")


def _assert_no_licensed_values(value: Any, *, what: str, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).lower()
            marker = next((m for m in LICENSED_FIELD_MARKERS if m in name), None)
            if marker is not None:
                raise SfiaLicenseError(
                    f"{what}: field {path + str(key)!r} names licensed SFIA prose "
                    f"(matched {marker!r}). Descriptions are fetched at runtime by "
                    "users holding their own SFIA access; they are never stored here."
                )
            _assert_no_licensed_values(item, what=what, path=f"{path}{key}.")
        return
    if isinstance(value, str):
        if len(value) > MAX_LABEL_CHARS:
            raise SfiaLicenseError(
                f"{what}: string at {path.rstrip('.')!r} is {len(value)} characters, over the "
                f"{MAX_LABEL_CHARS}-character label ceiling. A SFIA label is short; "
                "anything this long is prose, and prose may not be stored here."
            )
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _assert_no_licensed_values(item, what=what, path=f"{path}{index}.")


# --- HTML parsing ----------------------------------------------------------

# The A–Z directory table: one row per skill, columns Title / Skill code /
# Description. The title cell links to the skill's page, which is the only join
# between a page slug and a skill code that SFIA publishes.
_ROW = re.compile(r"(?s)<tr\b.*?</tr>")
_CELL = re.compile(r"(?s)<td\b[^>]*>(.*?)</td>")
_TITLE_LINK = re.compile(rf'href="[^"]*?/sfia-{VERSION}/skills/([a-z0-9\-]+)"')
_TAG = re.compile(r"(?s)<[^>]+>")

# Skill page: one section per level, then a sidebar of related skills.
_LEVEL_SECTION = re.compile(r'id="skill_level_section_(\d)"')
_LEVEL_TEXT = 'class="level_text"'
_AFTER_LEVELS = 'id="viewlet-below-content-body"'
_RELATED_LINK = re.compile(
    rf'(?s)class="related-skill-link".*?href="[^"]*?/sfia-{VERSION}/skills/([a-z0-9\-]+)"'
)


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_module.unescape(_TAG.sub(" ", fragment))).strip()


def _tag_start(page: str, position: int) -> int:
    """Start of the tag containing ``position``.

    Section boundaries are found by matching an ``id=…`` attribute, which sits
    *inside* a tag. Cutting there leaves half an opening tag at the end of the
    slice, and a half tag has no closing ``>`` for the tag-stripper to match —
    so ``<div class="skill_level "`` survives as visible text and an emptiness
    test reads it as content. That is not hypothetical: it is why an earlier
    revision reported all seven levels defined for all 147 skills.
    """
    start = page.rfind("<", 0, position)
    return position if start == -1 else start


def _after_tag(fragment: str, marker: str) -> int | None:
    """Offset just past the ``>`` of the tag carrying ``marker``, or None."""
    at = fragment.find(marker)
    if at == -1:
        return None
    close = fragment.find(">", at)
    return None if close == -1 else close + 1


def extract_directory(page: str) -> list[dict[str, str]]:
    """Skill code, name and page slug from the A–Z directory page.

    The directory table's third column is the skill's description. It is read
    here — it arrives in the same HTML — and then **not** carried into the
    returned row, because it is exactly the licensed text ADR-0006 §2 keeps out
    of this repository. Only cells 0 (title, and the link that gives the slug)
    and 1 (code) are used; the loop below never indexes past them.
    """
    out: list[dict[str, str]] = []
    for row in _ROW.findall(page):
        cells = _CELL.findall(row)
        if len(cells) < 2:
            continue  # the header row, and the trailing empty cell
        slug_match = _TITLE_LINK.search(cells[0])
        if slug_match is None:
            continue
        slug = slug_match.group(1)
        if slug == Path(SNAPSHOT_INDEX_FILE).stem:
            continue  # the page's link to itself
        name = _plain(cells[0])
        try:
            code = normalize_skill_code(_plain(cells[1]))
        except SfiaIdError:
            # A cell that is not a code means the table's column order changed;
            # skipping silently would produce a short, plausible-looking load.
            raise SfiaExtractError(
                f"directory row for {slug!r} has no skill code in column 2 "
                f"(found {_plain(cells[1])!r}); the published table's shape has changed"
            ) from None
        out.append({"code": code, "name": name, "slug": slug})
    if not out:
        raise SfiaExtractError("no skills found in the A–Z directory page")
    return out


def extract_skill_page(page: str) -> dict[str, Any]:
    """Which levels a skill is defined at, and its published related skills.

    SFIA renders all seven level sections on every skill page and fills in only
    the ones where that skill is defined — which is the framework's own point
    that skills are deliberately not defined at all seven levels. The *presence*
    of content in a section is the published structural fact this suite stores.

    The content itself is SFIA's definition of the skill at that level, the
    single most obviously licensed thing on the page. It is reduced to a boolean
    here, at the moment of reading, and never returned: what leaves this
    function is a list of integers.
    """
    marks = [(_tag_start(page, m.start()), int(m.group(1))) for m in _LEVEL_SECTION.finditer(page)]
    if not marks:
        raise SfiaExtractError("skill page has no level sections")
    after = page.find(_AFTER_LEVELS)
    end = len(page) if after == -1 else _tag_start(page, after)

    levels: list[int] = []
    for index, (start, level) in enumerate(marks):
        stop = marks[index + 1][0] if index + 1 < len(marks) else end
        body_at = _after_tag(page[start:stop], _LEVEL_TEXT)
        if body_at is None:
            continue
        # Everything after that tag to the end of this section. Non-empty means
        # "defined at this level"; the text itself stops here.
        if _plain(page[start:stop][body_at:]):
            levels.append(normalize_level(level))

    related = []
    seen: set[str] = set()
    for slug in _RELATED_LINK.findall(page[end:] if end < len(page) else page):
        if slug not in seen:
            seen.add(slug)
            related.append(slug)
    return {"levels": levels, "related_slugs": related}


# --- documents -------------------------------------------------------------


def read_snapshot(data_dir: Path) -> dict[str, Any]:
    """Build a structure-only document from a local snapshot of the SFIA pages.

    The public pages carry no category tree — SFIA's category and subcategory
    view is behind a login — so a document built this way has skills, levels and
    related-skill links but no groups. That is recorded in the document's meta
    rather than silently producing a graph with an empty category tree, and
    ``validate_load`` does not require categories for the same reason.
    """
    index_path = data_dir / SNAPSHOT_INDEX_FILE
    if not index_path.exists():
        raise FileNotFoundError(
            f"Missing {index_path}. Run `python -m ta_taxonomies.suites.sfia.fetch` "
            "to build a local snapshot under your own SFIA licence."
        )
    directory = extract_directory(index_path.read_text(encoding="utf-8"))
    codes_by_slug = {row["slug"]: row["code"] for row in directory}

    skills: list[dict[str, Any]] = []
    missing_pages: list[str] = []
    for row in directory:
        page_path = data_dir / SNAPSHOT_SKILLS_DIR / f"{row['slug']}.html"
        if not page_path.exists():
            missing_pages.append(row["slug"])
            skills.append({**row, "levels": [], "related_slugs": []})
            continue
        detail = extract_skill_page(page_path.read_text(encoding="utf-8"))
        skills.append({**row, **detail})

    return {
        "meta": {
            "version": VERSION,
            "mode": "snapshot",
            "data_dir": str(data_dir),
            "has_category_tree": False,
            "skill_pages_missing": len(missing_pages),
        },
        "skills": skills,
        "codes_by_slug": codes_by_slug,
    }


# Columns of the CSV a licensee can export from SFIA's spreadsheet distribution.
# `levels` is a separator-joined list of level numbers, e.g. "2;3;4;5;6".
CSV_COLUMNS = ("skill_code", "skill_name", "category", "subcategory", "levels")
CSV_LEVEL_SEPARATOR = ";"


def read_structure_csv(path: Path) -> dict[str, Any]:
    """Build a structure-only document from a licensee's own CSV export.

    This is the path that carries the category tree. Any column outside
    ``CSV_COLUMNS`` is ignored rather than loaded, so exporting a sheet that
    still has its description column does not put descriptions in the graph —
    the export step is where a user is most likely to hand over more than this
    repository may keep.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SfiaExtractError(f"{path} has no rows")
    missing = [c for c in ("skill_code", "skill_name") if c not in rows[0]]
    if missing:
        raise SfiaExtractError(f"{path} is missing required column(s): {missing}")

    skills: list[dict[str, Any]] = []
    for row in rows:
        levels_raw = (row.get("levels") or "").strip()
        levels = [
            normalize_level(part) for part in levels_raw.split(CSV_LEVEL_SEPARATOR) if part.strip()
        ]
        skills.append(
            {
                "code": normalize_skill_code(row["skill_code"]),
                "name": (row.get("skill_name") or "").strip(),
                "category": (row.get("category") or "").strip(),
                "subcategory": (row.get("subcategory") or "").strip(),
                "levels": levels,
                "related_slugs": [],
            }
        )
    return {
        "meta": {
            "version": VERSION,
            "mode": "structure_csv",
            "data_dir": str(path),
            "has_category_tree": any(s["category"] for s in skills),
            "skill_pages_missing": 0,
        },
        "skills": skills,
        "codes_by_slug": {},
    }


def read_source(data_dir: Path) -> dict[str, Any]:
    """Prefer a licensee's CSV export when present, else the page snapshot."""
    csv_path = data_dir / SNAPSHOT_STRUCTURE_CSV
    if csv_path.exists():
        return read_structure_csv(csv_path)
    return read_snapshot(data_dir)
