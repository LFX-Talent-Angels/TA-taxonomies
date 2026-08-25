"""BLS identity helpers: SOC codes → suite-scoped ids, and the SOC hierarchy.

Use case: turn the identifiers every BLS table joins on — the SOC code
(``15-1252``) and the National Employment Matrix industry code (``5415A1``) —
into stable graph keys, and derive the four-level SOC hierarchy that the spine
is made of.

Why it exists, and why it is the largest pure-function module in this suite:
**SOC codes carry most of their hierarchy in their digits — and not all of
it.** ``15-1252`` is a detailed occupation inside broad group ``15-1250``,
inside minor group ``15-1200``, inside major group ``15-0000``. The broad group
and the major group are read off the code, uniformly, everywhere in SOC. The
**minor group is not derivable**: minor groups come in two shapes, ``NN-X000``
(92 of the 95 BLS publishes) and ``NN-XY00`` (the other three), so ``29-1210``
sits under ``29-1000`` while ``15-1250`` sits under ``15-1200``. It has to be
looked up against the published set — see ``soc_minor_candidates``, which is
where that is argued at length, because assuming otherwise produces a tree that
loads, validates, and is one level wrong above the broad group.

The tree still needs no join table beyond that lookup, and every node carries
its own ancestors as properties. That is what makes this suite the spine: a
crosswalk arrives holding a detailed code and wants to roll up, and if roll-up
were only edges it would depend on which ancestor groups BLS happens to publish
a line for — it publishes 174 of the 450 broad groups its own detailed codes
imply. Storing the ancestor codes on the node makes roll-up exact everywhere,
and the edges then only have to be navigable.

Codes stay strings. ``15-1252`` read as a number is ``15 - 1252``; the leading
zeros of ``11-1011`` and the whole of ``00-0000`` do not survive a float.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any

from ta_taxonomies.suites.bls.config import (
    SOC_LEVEL_BROAD,
    SOC_LEVEL_DETAILED,
    SOC_LEVEL_MAJOR,
    SOC_LEVEL_MINOR,
    SOURCE,
)

# The published SOC code shape: two digits, hyphen, four digits.
_SOC = re.compile(r"^(\d{2})-(\d{4})$")
# The same code with the hyphen dropped, which is how ep.series ids, the
# OES tables and every "occ code without punctuation" column carry it.
_SOC_FLAT = re.compile(r"^(\d{2})(\d{4})$")
# O*NET-SOC extends a SOC code with a two-digit suffix (15-1252.00). Accepted
# on input for resolution only — see ``soc_code_from_any``.
_ONETSOC = re.compile(r"^(\d{2}-\d{4})\.(\d{2})$")
# NEM industry codes are mostly six digits but not always: '1131-2', '3250A1',
# '61110L' and 'TE1000' are all real. Anything non-empty without whitespace is
# accepted, because inventing a stricter rule would reject the source.
_INDUSTRY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")

# Any suite's id may be handed to ``soc_code_from_any``; this pulls the tail off
# ``onet:soc:15-1252`` or ``bls:occupation:15-1252`` without caring which suite
# wrote it. The join is on the SOC code string, never on the prefix.
_SUITE_ID_TAIL = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:(.+)$")


class BlsIdError(ValueError):
    """Raised when a source identifier does not have the documented shape."""


class NoSocCodeError(BlsIdError):
    """Raised when an identifier carries no SOC code at all.

    Distinct from ``BlsIdError`` on purpose. A malformed SOC code is a
    corrupted read; an ESCO occupation URI is a perfectly good identifier that
    simply has no SOC in it, and ARCHITECTURE.md is explicit that the answer
    there is a recorded "no link" rather than a guess. The caller needs to tell
    the two apart to report the right one.
    """


def code_to_str(value: Any) -> str | None:
    """Normalize a spreadsheet-typed code back to its exact source string.

    The BLS flat files are tab-delimited and never lossy, but crosswalk
    workbooks and hand-made fixtures round-trip through tools that type
    ``11-1011`` as text and ``151252`` as a number. A string is returned
    untouched, leading zeros included.
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


def normalize_soc_code(value: Any) -> str:
    """Validate a SOC code and return it hyphenated, exactly as BLS publishes it.

    Accepts the hyphenated form and the flat six-digit form, because BLS itself
    uses both — ``ep.occupation`` writes ``15-1252`` and ``oe.occupation``
    writes ``151252`` for the same occupation. Both normalize to the published
    hyphenated form so that one code is one identity.
    """
    text = code_to_str(value)
    if not text:
        raise BlsIdError("empty SOC code")
    match = _SOC.match(text)
    if match is None:
        match = _SOC_FLAT.match(text)
    if match is None:
        raise BlsIdError(f"not a SOC code: {text!r} (expected NN-NNNN or NNNNNN)")
    return f"{match.group(1)}-{match.group(2)}"


def soc_code_from_any(value: Any) -> str:
    """Extract the SOC code from whatever identifier a caller happens to hold.

    Accepts, in order of how a crosswalk actually arrives:

    ==============================  ==================
    ``15-1252`` / ``151252``        the code itself
    ``bls:occupation:15-1252``      this suite's id
    ``onet:soc:15-1252``            O*NET's SOC view
    ``onet:occupation:15-1252.00``  an O*NET-SOC code
    ``15-1252.00``                  the same, bare
    ==============================  ==================

    **This is a string identity on the SOC code, not a semantic mapping.**
    O*NET builds its 2019 taxonomy by appending a two-digit extension to a 2018
    SOC code and documents the decomposition, so the prefix of
    ``15-1252.00`` *is* SOC ``15-1252`` — the same identifier, not a claim that
    two concepts correspond. Anything with no SOC code in it raises
    ``NoSocCodeError``: ESCO is ISCO-aligned and has no SOC code to find, and
    bridging that gap is ``crosswalks/``' job with a published crosswalk, not
    this function's with a guess.

    Note that the O*NET extension is dropped, and that discards information: a
    SOC code split into several detailed O*NET occupations collapses to one
    node here. That is correct — there is one SOC ``15-1252`` — but a caller
    resolving ``onet:occupation:29-1141.01`` should know it landed on the SOC
    line, not on "Acute Care Nurses". ``resolve_soc`` says so in its result.
    """
    text = code_to_str(value)
    if not text:
        raise NoSocCodeError("empty identifier")

    tail = _SUITE_ID_TAIL.match(text)
    if tail is not None:
        text = tail.group(1)

    if _ONETSOC.match(text):
        text = text.split(".", 1)[0]

    try:
        return normalize_soc_code(text)
    except BlsIdError as exc:
        raise NoSocCodeError(
            f"no SOC code in {value!r}; this suite is SOC-native and does not "
            "infer one from a non-SOC identifier"
        ) from exc


def soc_level(value: Any) -> str:
    """Return which of the four SOC levels a code sits at.

    Read straight off the digits, as the SOC coding structure defines them:

    ==========  ========  ==========================================
    ``NN-0000`` major     15-0000 Computer and Mathematical
    ``NN-XY00`` minor     15-1200 Computer Occupations
    ``NN-XYZ0`` broad     15-1250 Software and Web Developers
    ``NN-XYZW`` detailed  15-1252 Software Developers
    ==========  ========  ==========================================

    A code ending in two zeros is a minor group or a major group, never a
    broad one: a broad group is a subdivision of a minor group, so at least one
    of the two digits it adds is non-zero. That is what makes the *level* — as
    opposed to the *parent* — derivable, and it holds for both minor-group
    shapes: ``11-1000`` (Top executives) and ``15-1200`` (Computer occupations)
    are both minor, and both are classified here without a lookup.

    Knowing a code's level is not knowing its minor group. See
    ``soc_minor_candidates``.
    """
    code = normalize_soc_code(value)
    body = code[3:]
    if body == "0000":
        return SOC_LEVEL_MAJOR
    if body[2:] == "00":
        return SOC_LEVEL_MINOR
    if body[3] == "0":
        return SOC_LEVEL_BROAD
    return SOC_LEVEL_DETAILED


def is_detailed_occupation(value: Any) -> bool:
    """True for a detailed SOC occupation, False for a major/minor/broad group."""
    return soc_level(value) == SOC_LEVEL_DETAILED


def soc_broad_code(value: Any) -> str | None:
    """The broad group a detailed code belongs to, or None above that level.

    ``15-1252`` → ``15-1250``. This one *is* derivable: a broad group is its
    detailed occupations with the last digit zeroed, uniformly, throughout SOC.
    """
    code = normalize_soc_code(value)
    if soc_level(code) != SOC_LEVEL_DETAILED:
        return None
    return code[:6] + "0"


def soc_major_code(value: Any) -> str | None:
    """The major group a code belongs to, or None if it is already one.

    ``15-1252`` → ``15-0000``. Also uniformly derivable: the major group is the
    first two digits, and nothing in SOC contradicts it.
    """
    code = normalize_soc_code(value)
    if soc_level(code) == SOC_LEVEL_MAJOR:
        return None
    return code[:3] + "0000"


def soc_minor_candidates(value: Any) -> list[str]:
    """The codes that could be this code's minor group, most specific first.

    **The minor group is the one level of SOC that a code does not determine**,
    and this is the single most consequential fact in this module. Minor groups
    come in two shapes and *both are real*:

    ======================================  =========================
    ``29-1000`` Healthcare Diagnosing …     ``NN-X000``, 92 of the 95
    ``15-1200`` Computer Occupations        ``NN-XY00``, the other 3
    ======================================  =========================

    So ``29-1210`` (Physicians) sits under ``29-1000`` while ``15-1250``
    (Software and Web Developers) sits under ``15-1200``. Zeroing the last two
    digits — which looks like the rule, and which the three-zero majority hides
    — would put Physicians under ``29-1200``, a code SOC does not define. Three
    major groups (15, 31 and 51) publish minor groups of *both* shapes, so
    there is not even a per-major rule to fall back on.

    The resolution is therefore a lookup, not a derivation, and this function
    returns the candidates for ``resolve_soc_minor`` to check against what BLS
    actually publishes. Nothing here picks one.
    """
    code = normalize_soc_code(value)
    if soc_level(code) in (SOC_LEVEL_MAJOR, SOC_LEVEL_MINOR):
        return []
    out: list[str] = []
    for candidate in (code[:5] + "00", code[:4] + "000"):
        if candidate not in out:
            out.append(candidate)
    return out


def resolve_soc_minor(value: Any, minor_groups: Collection[str]) -> str | None:
    """The minor group of a code, looked up in the published set.

    ``minor_groups`` is every minor-level code BLS publishes a line for. The
    more specific candidate wins when both are published, which is the only
    ordering consistent with SOC's nesting: ``NN-XY00`` is inside ``NN-X000``,
    so if both exist the code belongs to the inner one.

    Returns None when neither candidate is published, which is a real answer
    rather than a failure — it means BLS names no minor group for this code and
    the ``BROADER_THAN`` edge has to skip the level. On the 2024–34 round every
    one of the 1,006 published non-major codes resolves to exactly one minor
    group, so the None branch is currently unexercised by real data; it exists
    because the alternative is inventing a code, and an invented ancestor is
    indistinguishable from a real one once it is a node.
    """
    known = set(minor_groups)
    for candidate in soc_minor_candidates(value):
        if candidate in known:
            return candidate
    return None


def soc_ancestors(value: Any, minor_groups: Collection[str]) -> dict[str, str]:
    """Every SOC code above this one, keyed by level.

    ``15-1252`` → ``{'broad': '15-1250', 'minor': '15-1200', 'major': '15-0000'}``.

    ``minor_groups`` is required rather than optional on purpose: the minor
    group cannot be derived from the code (see ``soc_minor_candidates``), and a
    signature that let a caller omit it would let a caller get it wrong. There
    is no default that is right.

    These go onto the node as properties, which is what makes roll-up work
    regardless of whether BLS publishes a line for the intermediate group — it
    publishes 174 of the 450 broad groups its own detailed codes imply, so
    roll-up that could only follow edges would answer "no broad group" for most
    of the spine.
    """
    code = normalize_soc_code(value)
    out: dict[str, str] = {}
    broad = soc_broad_code(code)
    if broad is not None:
        out[SOC_LEVEL_BROAD] = broad
    minor = resolve_soc_minor(code, minor_groups)
    if minor is not None:
        out[SOC_LEVEL_MINOR] = minor
    major = soc_major_code(code)
    if major is not None:
        out[SOC_LEVEL_MAJOR] = major
    # An ancestor equal to the code would make the node its own parent and the
    # BROADER_THAN tree cyclic. soc_level already places every code, so this
    # cannot fire today; it is here because the derivations above zero digits
    # the level below still holds, and that is an argument, not a test.
    return {level_name: parent for level_name, parent in out.items() if parent != code}


def soc_parent_chain(value: Any, minor_groups: Collection[str]) -> list[str]:
    """Ancestor codes nearest-first: broad, then minor, then major."""
    ancestors = soc_ancestors(value, minor_groups)
    order = (SOC_LEVEL_BROAD, SOC_LEVEL_MINOR, SOC_LEVEL_MAJOR)
    return [ancestors[level] for level in order if level in ancestors]


def nearest_present_parent(
    value: Any, present: Collection[str], minor_groups: Collection[str]
) -> str | None:
    """The closest ancestor code that exists in ``present``, or None.

    BLS does not publish a line for every group its codes imply, so the
    ``BROADER_THAN`` edge goes to the nearest ancestor that is actually a node.
    The skipped levels are not lost — they are on the node as ``soc_broad`` /
    ``soc_minor`` / ``soc_major``, and the edge carries how many levels it
    jumped so a reader can see where the tree is not uniform.
    """
    known = set(present)
    for parent in soc_parent_chain(value, minor_groups):
        if parent in known:
            return parent
    return None


def normalize_industry_code(value: Any) -> str:
    """Validate a National Employment Matrix industry code.

    Deliberately permissive: ``1131-2``, ``3250A1``, ``61110L`` and ``TE1000``
    are all real codes in ``ep.industry``. A tighter rule would have to reject
    part of the source, and a code this loader cannot represent is a reason to
    fail loudly, not to invent a shape for.
    """
    text = code_to_str(value)
    if not text:
        raise BlsIdError("empty industry code")
    if not _INDUSTRY.match(text):
        raise BlsIdError(f"not a NEM industry code: {text!r}")
    return text


def occupation_id(code: Any) -> str:
    """``15-1252`` → ``bls:occupation:15-1252`` (detailed SOC only)."""
    normalized = normalize_soc_code(code)
    if not is_detailed_occupation(normalized):
        raise BlsIdError(
            f"{normalized} is a {soc_level(normalized)} SOC group, not a detailed "
            "occupation; use soc_group_id"
        )
    return f"{SOURCE}:occupation:{normalized}"


def soc_group_id(code: Any) -> str:
    """``15-1200`` → ``bls:soc:15-1200`` (major / minor / broad groups)."""
    normalized = normalize_soc_code(code)
    if is_detailed_occupation(normalized):
        raise BlsIdError(
            f"{normalized} is a detailed SOC occupation, not a group; use occupation_id"
        )
    return f"{SOURCE}:soc:{normalized}"


def soc_node_id(code: Any) -> str:
    """The id of whichever node kind a SOC code belongs to.

    One entry point for callers that hold a code and do not yet know its level
    — which is every crosswalk, since the level is a property of the code they
    were handed rather than something they chose.
    """
    normalized = normalize_soc_code(code)
    if is_detailed_occupation(normalized):
        return occupation_id(normalized)
    return soc_group_id(normalized)


def industry_id(code: Any) -> str:
    """``5415A1`` → ``bls:industry:5415A1``."""
    return f"{SOURCE}:industry:{normalize_industry_code(code)}"


def dedupe_titles(titles: Any) -> list[str]:
    """Collapse an alias pool to unique, non-empty, order-preserving strings.

    ``ep.laytitle`` publishes 5,820 everyday job titles across 817 SOC codes
    and an occupation's own title can reappear among them. Order is preserved
    so a fixture regenerated from the same source is byte-identical.
    """
    if titles is None:
        return []
    if isinstance(titles, str):
        titles = [titles]
    seen: dict[str, None] = {}
    for raw in titles:
        text = str(raw or "").strip()
        if not text or text == "-":
            continue
        seen.setdefault(text, None)
    return list(seen)
