"""O*NET identity helpers: codes → suite-scoped ids. Pure functions, no I/O.

Use case: turn the two identifiers every O*NET table joins on — the
``O*NET-SOC Code`` (``15-1252.00``) and the Content Model ``Element ID``
(``2.B.3.e``) — into stable suite-scoped graph keys, and derive the 2018 SOC
code and Content Model parentage that the graph's hierarchy edges need.

Why it exists: ESCO's identity arrives as a URI that already names its own
type (``…/esco/occupation/<uuid>``). O*NET's arrives as a bare code whose type
is implied by the file it came from and whose *structure* carries meaning —
the part of ``15-1252.00`` before the dot is a real SOC code, and ``2.B.3.e``
is literally a path through the Content Model tree. Both facts are exploited
here, once, instead of by regex at each call site.

Codes stay strings throughout. ``15-1252.00`` read as a float is 15 minus
1252.0; read as "a number with a decimal point" it loses the trailing zero and
stops matching the source. Neither is recoverable afterwards.
"""

from __future__ import annotations

import re
from typing import Any

from ta_taxonomies.suites.onet.config import SOURCE

# O*NET-SOC 2019: two digits, hyphen, four digits, dot, two digits. The final
# pair is the O*NET extension: ".00" means the occupation is coextensive with
# its SOC code; ".01" and up are detailed O*NET occupations that subdivide one.
_ONETSOC = re.compile(r"^(\d{2}-\d{4})\.(\d{2})$")

# Content Model Element ID: dot-separated segments, e.g. "2", "2.B", "2.B.3",
# "2.B.3.e", "1.A.1.a.1". Segments are digits or a single letter.
_ELEMENT_ID = re.compile(r"^[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*$")


class OnetIdError(ValueError):
    """Raised when a source identifier does not have the documented shape."""


def code_to_str(value: Any) -> str | None:
    """Normalize a spreadsheet-typed code back to its exact source string.

    O*NET's own text distribution is tab-delimited and never lossy, but the
    crosswalk workbooks and any hand-made fixture round-trip through tools that
    type ``15-1252.00`` as text and ``2051`` as a number. Anything that already
    is a string is returned untouched — including its leading zeros.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.10f}".rstrip("0").rstrip(".")
    text = str(value).strip()
    return text or None


def normalize_onetsoc_code(value: Any) -> str:
    """Validate an O*NET-SOC code and return it exactly as published.

    Deliberately strict: a code that does not match ``NN-NNNN.NN`` is a
    corrupted read, not something to repair by guessing. All 1,016 codes in
    release 30.3 match, so a failure here means the input is wrong.
    """
    text = code_to_str(value)
    if not text:
        raise OnetIdError("empty O*NET-SOC code")
    if not _ONETSOC.match(text):
        raise OnetIdError(f"not an O*NET-SOC code: {text!r} (expected NN-NNNN.NN)")
    return text


def soc_code_from_onetsoc(value: Any) -> str:
    """Return the 2018 SOC code an O*NET-SOC code extends.

    The O*NET Center builds the 2019 O*NET-SOC taxonomy on the 2018 SOC by
    appending a two-digit extension, so the SOC code is the published prefix —
    a documented decomposition, not an inference. It is still *O*NET's* view of
    SOC: the authoritative SOC nodes belong to the BLS suite, and linking the
    two is a crosswalk's job, not this suite's.
    """
    return normalize_onetsoc_code(value).split(".", 1)[0]


def is_detailed_occupation(value: Any) -> bool:
    """True when the code subdivides its SOC code (extension other than .00)."""
    return normalize_onetsoc_code(value).split(".", 1)[1] != "00"


def normalize_element_id(value: Any) -> str:
    """Validate a Content Model Element ID and return it as published."""
    text = code_to_str(value)
    if not text:
        raise OnetIdError("empty Element ID")
    if not _ELEMENT_ID.match(text):
        raise OnetIdError(f"not a Content Model Element ID: {text!r}")
    return text


def element_parent_id(value: Any) -> str | None:
    """Return the Element ID one level up, or None at the top of a branch.

    ``2.B.3.e`` → ``2.B.3`` → ``2.B`` → ``2`` → None. The Content Model tree is
    encoded in the identifier itself, so the hierarchy needs no join table.
    """
    text = normalize_element_id(value)
    head, sep, _tail = text.rpartition(".")
    return head if sep else None


def element_ancestors(value: Any) -> list[str]:
    """Every Element ID above this one, nearest first."""
    out: list[str] = []
    current = element_parent_id(value)
    while current is not None:
        out.append(current)
        current = element_parent_id(current)
    return out


def occupation_id(code: Any) -> str:
    """``15-1252.00`` → ``onet:occupation:15-1252.00``."""
    return f"{SOURCE}:occupation:{normalize_onetsoc_code(code)}"


def soc_group_id(code: Any) -> str:
    """``15-1252`` or ``15-1252.00`` → ``onet:soc:15-1252``."""
    text = code_to_str(code) or ""
    if _ONETSOC.match(text):
        text = soc_code_from_onetsoc(text)
    if not re.match(r"^\d{2}-\d{4}$", text):
        raise OnetIdError(f"not a SOC code: {text!r} (expected NN-NNNN)")
    return f"{SOURCE}:soc:{text}"


def element_id(value: Any) -> str:
    """``2.B.3.e`` → ``onet:element:2.B.3.e`` (skills, knowledge, abilities)."""
    return f"{SOURCE}:element:{normalize_element_id(value)}"


def task_id(value: Any) -> str:
    """``21662`` → ``onet:task:21662``.

    Task IDs are integers in the source but are kept as strings for the same
    reason every other code is: an identifier that is never arithmetic has no
    business being a number.
    """
    text = code_to_str(value)
    if not text or not text.isdigit():
        raise OnetIdError(f"not a Task ID: {value!r}")
    return f"{SOURCE}:task:{text}"


def dedupe_titles(titles: Any) -> list[str]:
    """Collapse an alias pool to unique, non-empty, order-preserving strings.

    O*NET publishes lay titles in two overlapping files (57,543 "Job Titles"
    and 7,953 "Sample of Reported Titles"), and an occupation's preferred title
    reappears among them. Order is preserved so a fixture regenerated from the
    same source produces byte-identical output.
    """
    if titles is None:
        return []
    if isinstance(titles, str):
        titles = [titles]
    seen: dict[str, None] = {}
    for raw in titles:
        text = str(raw or "").strip()
        # "n/a" is O*NET's null in these columns, not a job anyone holds.
        if not text or text.lower() == "n/a":
            continue
        seen.setdefault(text, None)
    return list(seen)
