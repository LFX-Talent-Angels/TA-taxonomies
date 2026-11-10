"""O*NET identity helpers: native codes → suite-scoped ids.

Use case: during load (and anywhere we touch source rows), turn O*NET-SOC
codes, Content Model Element IDs, task ids, and software example names into
stable graph keys such as ``onet:occupation:15-1252.00``.

Why it exists: architecture requires suite-scoped string ids; SOC codes keep
their trailing ``.00``; software examples have no native code so we slug the
name. Pure functions — no Neo4j access.
"""

from __future__ import annotations

import re

from ta_taxonomies.suites.onet.config import SOURCE

_SLUG_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


class OnetIdError(ValueError):
    """Raised when a native O*NET identifier cannot become a suite id."""


def _require_token(value: str, *, what: str) -> str:
    text = str(value).strip()
    if not text:
        raise OnetIdError(f"empty {what}")
    return text


def suite_id_occupation(soc_code: str) -> str:
    """``15-1252.00`` → ``onet:occupation:15-1252.00`` (string; never a float)."""
    return f"{SOURCE}:occupation:{_require_token(soc_code, what='O*NET-SOC code')}"


def suite_id_element(element_id: str) -> str:
    """Content Model Element ID → ``onet:element:2.B.3.e``."""
    return f"{SOURCE}:element:{_require_token(element_id, what='Element ID')}"


def suite_id_task(task_id: str) -> str:
    return f"{SOURCE}:task:{_require_token(task_id, what='Task ID')}"


def suite_id_job_zone(zone: str) -> str:
    return f"{SOURCE}:job-zone:{_require_token(zone, what='Job Zone')}"


def suite_id_scale(scale_id: str) -> str:
    return f"{SOURCE}:scale:{_require_token(scale_id, what='Scale ID')}"


def software_slug(name: str) -> str:
    """Stable id fragment for a Workplace Example that has no O*NET code.

    ``#`` and ``+`` are kept as words so ``C`` and ``C#`` (or ``C++``) do not
    collapse to the same id.
    """
    text = _require_token(name, what="software name").lower()
    text = text.replace("#", "-sharp").replace("+", "-plus")
    slug = _SLUG_SPLIT.sub("-", text).strip("-")
    if not slug:
        raise OnetIdError(f"software name {name!r} slugs to empty")
    return slug


def suite_id_software(name: str) -> str:
    return f"{SOURCE}:software:{software_slug(name)}"
