"""JobTech identity helpers: concept ids and SSYK codes → suite-scoped ids.

JobTech concept ids are 12-character opaque strings ("5qT8_z9d_8rw"), stable
across taxonomy versions. SSYK 2012 codes are 1-4 digits, one per level.
``jobtech:occupation:<concept id>``, ``jobtech:ssyk:<code>``,
``jobtech:skill:<concept id>``, ``jobtech:skill-headline:<concept id>``.
Pure functions; no Neo4j access.
"""

from __future__ import annotations

import re
from typing import Any

from ta_taxonomies.suites.jobtech.config import SOURCE

CONCEPT_ID = re.compile(r"[A-Za-z0-9]{4}_[A-Za-z0-9]{3}_[A-Za-z0-9]{3}")
SSYK_CODE = re.compile(r"[0-9]{1,4}")


class JobtechIdError(ValueError):
    """Raised when a native JobTech value cannot become a suite id."""


def concept_id(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not CONCEPT_ID.fullmatch(text):
        raise JobtechIdError(f"not a JobTech concept id: {value!r}")
    return text


def ssyk_code(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not SSYK_CODE.fullmatch(text):
        raise JobtechIdError(f"not an SSYK 2012 code: {value!r}")
    return text


def occupation_id(cid: Any) -> str:
    return f"{SOURCE}:occupation:{concept_id(cid)}"


def ssyk_id(code: Any) -> str:
    """SSYK group by its code: ``7612`` → ``jobtech:ssyk:7612``."""
    return f"{SOURCE}:ssyk:{ssyk_code(code)}"


def skill_id(cid: Any) -> str:
    return f"{SOURCE}:skill:{concept_id(cid)}"


def headline_id(cid: Any) -> str:
    return f"{SOURCE}:skill-headline:{concept_id(cid)}"


#: "SSYK 7612", "ssyk-kod 7612", "SSYK: 7612" name the code too. In chat a bare
#: number is a pick from the list, so the scheme's name is how users say "code".
_SSYK_PREFIX = re.compile(r"^ssyk(?:[- ]?(?:2012|kod|code))?\s*[:#]?\s*", re.IGNORECASE)


def codes_in_query(text: str) -> list[str]:
    """Codes a search names: an SSYK code ("7612", "SSYK 7612") or a concept id."""
    q = _SSYK_PREFIX.sub("", text.strip(), count=1)
    return [q] if SSYK_CODE.fullmatch(q) or CONCEPT_ID.fullmatch(q) else []
