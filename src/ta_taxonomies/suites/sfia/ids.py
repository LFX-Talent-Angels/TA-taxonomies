"""SFIA identity helpers: skill codes, levels and slugs → suite-scoped ids.

Use case: during load (and anywhere we touch source rows), turn SFIA
structure into stable graph keys — e.g. skill code ``PROG`` →
``sfia:skill:PROG``, level 4 of PROG → ``sfia:level:PROG:4``.

Why it exists: architecture requires suite-scoped string ids and string codes.
SFIA codes collide with other sources (SFIA's ``ISCO`` skill is not the ISCO
occupation classification), so every node also carries ``source`` +
``source_id``. Pure functions — no Neo4j access.
"""

from __future__ import annotations

import re
from typing import Any

from ta_taxonomies.suites.sfia.config import (
    FRAMEWORK,
    MAX_LEVEL,
    MIN_LEVEL,
    SKILL_URL_BASE,
    SOURCE,
)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def code_to_str(value: Any) -> str:
    """Normalize a skill code to its stored form: a stripped, upper-case string."""
    if value is None:
        raise ValueError("empty SFIA skill code")
    text = str(value).strip().upper()
    if not text:
        raise ValueError("empty SFIA skill code")
    return text


def level_to_int(value: Any) -> int:
    """Return a responsibility level as an int in 1..7 (rejects anything else)."""
    if isinstance(value, bool):
        raise ValueError(f"invalid SFIA level: {value!r}")
    level = int(value)
    if not MIN_LEVEL <= level <= MAX_LEVEL or float(value) != level:
        raise ValueError(f"invalid SFIA level: {value!r}")
    return level


def slugify(text: str) -> str:
    """Lower-case, hyphen-separated slug ("Strategy and planning" → "strategy-and-planning")."""
    slug = _SLUG_STRIP.sub("-", (text or "").lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot slugify {text!r}")
    return slug


def skill_id(code: Any) -> str:
    """``PROG`` → ``sfia:skill:PROG``."""
    return f"{SOURCE}:skill:{code_to_str(code)}"


def level_id(code: Any, level: Any) -> str:
    """(``PROG``, 4) → ``sfia:level:PROG:4``."""
    return f"{SOURCE}:level:{code_to_str(code)}:{level_to_int(level)}"


def level_source_id(code: Any, level: Any) -> str:
    """(``PROG``, 4) → ``PROG-4`` (the native way SFIA users write a skill level)."""
    return f"{code_to_str(code)}-{level_to_int(level)}"


def category_id(slug: str) -> str:
    """``strategy-and-architecture`` → ``sfia:category:strategy-and-architecture``."""
    return f"{SOURCE}:category:{slugify(slug)}"


def subcategory_id(slug: str) -> str:
    """``systems-development`` → ``sfia:subcategory:systems-development``."""
    return f"{SOURCE}:subcategory:{slugify(slug)}"


def skill_url(slug: str) -> str:
    """Pointer to the skill's page on sfia-online.org (where the licensed text lives)."""
    return f"{SKILL_URL_BASE}{slugify(slug)}"


def skill_ref(code: Any) -> str:
    """Citation for a skill: ``SFIA 9 · PROG``."""
    return f"{FRAMEWORK} · {code_to_str(code)}"


def level_ref(code: Any, level: Any) -> str:
    """Citation for a skill level: ``SFIA 9 · PROG level 4``."""
    return f"{FRAMEWORK} · {code_to_str(code)} level {level_to_int(level)}"
