"""BLS identity helpers: SOC codes, EP skill ids and category names → suite ids.

Use case: during fetch and load, turn BLS codes into stable graph keys —
SOC ``15-1252`` → ``bls:occupation:15-1252``, broad group ``15-1250`` →
``bls:soc-group:15-1250``, EP skill "Critical and analytical thinking" →
``bls:skill:critical-and-analytical-thinking``.

Why it exists: ids are suite-scoped strings. A SOC code is also used by O*NET
(``15-1252.00``), but the two suites never share a node; the code is stored as
a property, which is how a crosswalk can join them later. Pure functions.
"""

from __future__ import annotations

import re
from typing import Any

from ta_taxonomies.suites.bls.config import SOURCE

#: A SOC 2018 code: two digits, a hyphen, four digits ("15-1252", "15-0000").
SOC_CODE = re.compile(r"[0-9]{2}-[0-9]{4}")
#: An O*NET-SOC code a user may paste ("15-1252.00"); BLS works at the base code.
ONET_SOC_CODE = re.compile(r"([0-9]{2}-[0-9]{4})\.[0-9]{2}")

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class BlsIdError(ValueError):
    """Raised when a native BLS value cannot become a suite id."""


def soc_code(value: Any) -> str:
    """Return a SOC code in its stored form, or raise ``BlsIdError``."""
    text = str(value if value is not None else "").strip()
    if not SOC_CODE.fullmatch(text):
        raise BlsIdError(f"not a SOC 2018 code: {value!r}")
    return text


def soc_level(code: str) -> str:
    """The SOC level a code names: major, minor, broad or detailed.

    ``15-0000`` major · ``15-1200`` minor · ``15-1250`` broad · ``15-1252``
    detailed. (Codes like ``15-1200`` are minor even though they end in "00".)
    """
    code = soc_code(code)
    tail = code[3:]
    if tail == "0000":
        return "major"
    if tail.endswith("00"):
        return "minor"
    if tail.endswith("0"):
        return "broad"
    return "detailed"


def slugify(text: str) -> str:
    """Lower-case, hyphen-separated slug ("Bachelor's degree" → "bachelor-s-degree")."""
    slug = _SLUG_STRIP.sub("-", (text or "").lower()).strip("-")
    if not slug:
        raise BlsIdError(f"cannot slugify {text!r}")
    return slug


def occupation_id(code: Any) -> str:
    """``15-1252`` → ``bls:occupation:15-1252``."""
    return f"{SOURCE}:occupation:{soc_code(code)}"


def group_id(code: Any) -> str:
    """``15-1250`` → ``bls:soc-group:15-1250``."""
    return f"{SOURCE}:soc-group:{soc_code(code)}"


def skill_id(name: str) -> str:
    """EP skill name → ``bls:skill:<slug>``."""
    return f"{SOURCE}:skill:{slugify(name)}"


def education_id(name: str) -> str:
    """Typical entry education → ``bls:education:<slug>``."""
    return f"{SOURCE}:education:{slugify(name)}"


def training_id(name: str) -> str:
    """Typical on-the-job training → ``bls:training:<slug>``."""
    return f"{SOURCE}:training:{slugify(name)}"


#: "SOC 15-1252", "soc code 15-1252", "SOC: 15-1252" name the code too.
_SOC_PREFIX = re.compile(r"^(?:o\*?net[- ]?)?soc(?:\s+code)?\s*[:#]?\s*", re.IGNORECASE)


def codes_in_query(text: str) -> list[str]:
    """SOC codes a search query names: ``15-1252``, ``15-1252.00`` or ``SOC 15-1252``."""
    q = _SOC_PREFIX.sub("", text.strip(), count=1)
    if SOC_CODE.fullmatch(q):
        return [q]
    match = ONET_SOC_CODE.fullmatch(q)
    return [match.group(1)] if match else []
