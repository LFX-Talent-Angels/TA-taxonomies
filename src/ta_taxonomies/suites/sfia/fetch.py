"""Fetch the SFIA 9 structure from the public "skills at a glance" page.

Use case: the ``fetch`` stage of the SFIA pipeline. Downloads the public page
(no login, stdlib ``urllib`` only) and writes the structure JSON that
``load.py --mode full`` reads::

    python -m ta_taxonomies.suites.sfia.fetch --out data/sfia/sfia9_structure.json

Licensing — read before changing this file: SFIA's free license covers
internal use only; redistributing SFIA material needs a fee-bearing license.
This parser therefore extracts ONLY unprotected structure: category and
subcategory names + slugs, skill codes, skill names, skill page slugs, and the
responsibility levels (1-7) each skill is defined at. It never reads or keeps
skill descriptions, level descriptions or guidance notes. The output stays
under the gitignored ``data/`` directory. The official SFIA spreadsheet
(behind free registration) is not needed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ta_taxonomies.suites.sfia.config import FRAMEWORK, GLANCE_URL, SOURCE

USER_AGENT = "ta-taxonomies-sfia-fetch/0.1 (+https://github.com/LFX-Talent-Angels)"
TIMEOUT_S = 60


class GlanceParser(HTMLParser):
    """Collect the cells of the glance table: one ``<tr class="cat-…">`` per skill."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[dict[str, Any]]] = []
        self._cell: dict[str, Any] | None = None
        self._in_row = False
        self._text_target: str | None = None  # "link" | "code" | "level"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "tr" and cls.startswith("cat-"):
            self._in_row = True
            self.rows.append([])
        elif self._in_row and tag == "td":
            self._cell = {"class": cls, "text": "", "href": None, "code": None}
            self.rows[-1].append(self._cell)
            if "level" in cls:
                self._text_target = "level"
        elif self._in_row and tag == "a" and self._cell is not None:
            self._cell["href"] = a.get("href")
            self._text_target = "link"
        elif self._in_row and tag == "span" and cls == "code" and self._cell is not None:
            self._text_target = "code"

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr":
            self._in_row = False
            self._cell = None
        elif tag in ("a", "span", "td"):
            self._text_target = None

    def handle_data(self, data: str) -> None:
        if not self._in_row or self._cell is None:
            return
        text = data.strip()
        if not text:
            return
        if self._text_target == "code":
            self._cell["code"] = text
        elif self._text_target in ("link", "level"):
            self._cell["text"] += text


def _slug_from_href(href: str | None) -> str | None:
    return href.rstrip("/").split("/")[-1] if href else None


def parse_glance(html: str) -> dict[str, Any]:
    """Turn the glance page HTML into the structure document (no SFIA prose)."""
    parser = GlanceParser()
    parser.feed(html)

    skills: list[dict[str, Any]] = []
    cur_cat: dict[str, Any] | None = None
    cur_sub: dict[str, Any] | None = None
    for row in parser.rows:
        level_cells = [c for c in row if "level" in c["class"]]
        other = [c for c in row if "level" not in c["class"]]
        if len(other) < 3:
            continue
        levels = sorted(
            int(c["text"]) for c in level_cells if "active" in c["class"] and c["text"].isdigit()
        )
        # Every row has 3 non-level cells: category, subcategory, skill. On
        # continuation rows the first two are empty -> carry forward.
        cat_cell, sub_cell, skill_cell = other[0], other[1], other[-1]
        if cat_cell["text"]:
            cur_cat = {"name": cat_cell["text"], "slug": _slug_from_href(cat_cell["href"])}
        if sub_cell["text"]:
            cur_sub = {"name": sub_cell["text"], "slug": _slug_from_href(sub_cell["href"])}
        skills.append(
            {
                "code": skill_cell["code"],
                "name": skill_cell["text"],
                "slug": _slug_from_href(skill_cell["href"]),
                "category": cur_cat["name"] if cur_cat else None,
                "category_slug": cur_cat["slug"] if cur_cat else None,
                "subcategory": cur_sub["name"] if cur_sub else None,
                "subcategory_slug": cur_sub["slug"] if cur_sub else None,
                "levels": levels,
            }
        )

    cats = sorted({(s["category"], s["category_slug"]) for s in skills})
    subs = sorted({(s["category"], s["subcategory"], s["subcategory_slug"]) for s in skills})
    return {
        "meta": {
            "suite": SOURCE,
            "framework": FRAMEWORK,
            "source_url": GLANCE_URL,
            "note": (
                "Structure only: codes, names, slugs, level numbers. "
                "No SFIA descriptive text (licensing). Pointer, not payload."
            ),
        },
        "categories": [{"name": n, "slug": sl} for n, sl in cats],
        "subcategories": [{"category": c, "name": n, "slug": sl} for c, n, sl in subs],
        "skills": skills,
    }


def incomplete_skills(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Skills the parser could not fill in (page layout changed?)."""
    return [
        s
        for s in doc["skills"]
        if not s["code"] or not s["levels"] or not s["category"] or not s["subcategory"]
    ]


def download(url: str = GLANCE_URL) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the SFIA 9 structure (codes, levels)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/sfia/sfia9_structure.json"),
        help="Where to write the structure JSON (keep it under gitignored data/)",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=None,
        help="Parse a saved copy of the glance page instead of downloading it",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        html = args.html.read_text(encoding="utf-8") if args.html else download()
    except OSError as exc:
        print(f"error: could not read the SFIA glance page: {exc}", file=sys.stderr)
        return 1
    doc = parse_glance(html)
    bad = incomplete_skills(doc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": not bad and bool(doc["skills"]),
                "out": str(args.out),
                "skills": len(doc["skills"]),
                "skill_levels": sum(len(s["levels"]) for s in doc["skills"]),
                "subcategories": len(doc["subcategories"]),
                "categories": len(doc["categories"]),
                "incomplete": [s.get("code") or s.get("name") for s in bad[:10]],
            },
            indent=2,
        )
    )
    return 0 if not bad and doc["skills"] else 1


if __name__ == "__main__":
    sys.exit(main())
