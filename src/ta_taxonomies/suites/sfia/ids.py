"""SFIA identity helpers: codes → suite-scoped ids. Pure functions, no I/O.

Use case: turn the two things SFIA actually identifies — a four-letter skill
code (``PROG``) and a level of responsibility (``4``) — into stable
suite-scoped graph keys, plus the derived keys for the category tree.

Why it exists, and it is the reason ADR-0006 §5 exists at all:

    **SFIA's codes are four bare letters with no namespace of their own.**
    ``ISCO`` in SFIA is *Information systems coordination*. ``ISCO`` elsewhere
    in this graph is the ILO's occupation classification that ESCO aligns to.
    They share four characters and nothing else. A bare code is therefore never
    an identity in the Talent Angels graph — only ``sfia:skill:ISCO`` is — and
    every node also carries ``source`` and ``source_id`` so the provenance
    survives even if an id is ever copied somewhere it should not be.

Codes stay strings and stay uppercase exactly as published. A level number is
the one thing here that *is* a number, and it is stored as one because it is
ordinal: level 4 is genuinely less than level 7, which is the whole point of
this suite.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ta_taxonomies.suites.sfia.config import (
    LEVEL_MAX,
    LEVEL_MIN,
    SKILL_CODE_LENGTH,
    SOURCE,
)

_SKILL_CODE = re.compile(rf"^[A-Z]{{{SKILL_CODE_LENGTH}}}$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class SfiaIdError(ValueError):
    """Raised when a source identifier does not have the documented shape."""


def normalize_skill_code(value: Any) -> str:
    """Validate a SFIA skill code and return it as published (four uppercase letters).

    Deliberately strict: all 147 codes in SFIA 9 are exactly four letters, so
    anything else is a corrupted read rather than something to repair by
    guessing. Lowercase input is upper-cased — users type ``prog`` — but a code
    of the wrong length is refused, because a shorter or longer string is a
    different kind of thing and silently accepting it would put a non-code into
    the id namespace.
    """
    text = str(value or "").strip()
    if not text:
        raise SfiaIdError("empty SFIA skill code")
    upper = text.upper()
    if not _SKILL_CODE.match(upper):
        raise SfiaIdError(f"not a SFIA skill code: {text!r} (expected {SKILL_CODE_LENGTH} letters)")
    return upper


def normalize_level(value: Any) -> int:
    """Validate a level of responsibility and return it as an int in 1–7.

    Levels are ordinal and bounded by the framework itself, so a value outside
    the range is not a level SFIA has ever defined. Accepting one would put a
    node in the graph that no responsibility axis can place.
    """
    if isinstance(value, bool):
        raise SfiaIdError(f"not a SFIA level: {value!r}")
    try:
        level = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise SfiaIdError(f"not a SFIA level: {value!r}") from exc
    if not (LEVEL_MIN <= level <= LEVEL_MAX):
        raise SfiaIdError(f"level {level} outside SFIA's {LEVEL_MIN}–{LEVEL_MAX} range")
    return level


def slugify(value: Any) -> str:
    """Lowercase ASCII slug for names that have no published code.

    SFIA gives categories and subcategories names but no identifiers, so their
    ids are derived from the name. That makes the id sensitive to a rename,
    which is stated in NOTES.md rather than hidden: a category renamed between
    SFIA versions produces a different node, and the ``version`` property on
    every node is what tells the two apart.
    """
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    slug = _SLUG_STRIP.sub("-", text.lower()).strip("-")
    if not slug:
        raise SfiaIdError(f"cannot slugify {value!r}")
    return slug


def skill_id(code: Any) -> str:
    """``PROG`` → ``sfia:skill:PROG``."""
    return f"{SOURCE}:skill:{normalize_skill_code(code)}"


def level_id(value: Any) -> str:
    """``4`` → ``sfia:level:4``."""
    return f"{SOURCE}:level:{normalize_level(value)}"


def category_id(name: Any) -> str:
    """``Development and implementation`` → ``sfia:category:development-and-implementation``."""
    return f"{SOURCE}:category:{slugify(name)}"


def subcategory_id(category: Any, name: Any) -> str:
    """Subcategory id, scoped by its category.

    Scoping is not cosmetic. A subcategory name is only unique *within* its
    category in SFIA, so an unscoped slug would MERGE two different groups from
    two different categories into one node — and every count would still add up,
    which is the failure mode this repo has already been bitten by twice.
    """
    return f"{SOURCE}:subcategory:{slugify(category)}/{slugify(name)}"


def skill_slug_to_code(slug: Any, codes_by_slug: dict[str, str]) -> str | None:
    """Resolve a published page slug (``programming-software-development``) to a code.

    SFIA's related-skill links point at page slugs rather than codes, so the
    A–Z directory is the only join between the two. A slug with no entry
    returns ``None`` and the caller drops the edge: a related-skill link whose
    target is outside the loaded slice is a missing endpoint, not a new node to
    invent.
    """
    return codes_by_slug.get(str(slug or "").strip().lower())
