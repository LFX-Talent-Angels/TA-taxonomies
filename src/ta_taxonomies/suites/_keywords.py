"""Keyword query for Locate Tier 5, the sentence fallback.

Tier 5 is reached when no label contains the whole query ("an engineer who
builds bridges"). It fuses this keyword list with the vector list; before,
the keyword side was always empty, so a sentence found only what the label
embeddings happened to match.
"""

from __future__ import annotations

import re

_TERM_SPLIT = re.compile(r"[\W_]+", re.UNICODE)

#: Words that carry no topic. The full-text indexes keep stop words, so
#: without this "who" and "the" would match half the graph.
_STOP_WORDS = frozenset(
    """
    a about after also am an and any are as at be been being but by can could
    do does doing for from get had has have how i i'd i'm if in into is it its
    job jobs just like look looking me more my not of on one or our out over
    really so some something such than that the their them then there these
    they thing things this those to too up us very want wants was we were what
    when where which who whom why will with work works working would you your
    """.split()
)

#: Longest first, so "buildings" loses "ings" rather than "s".
_SUFFIXES = ("ings", "ing", "ers", "er", "es", "ed", "s")
_MIN_STEM = 4
#: Enough terms to describe a job; a long paragraph adds noise, not recall.
MAX_TERMS = 8


def _stem(term: str) -> str:
    for suffix in _SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= _MIN_STEM:
            return term[: -len(suffix)]
    return term


def keyword_query(q: str) -> str | None:
    """An OR-of-prefixes Lucene query for the topic words in ``q``, or None.

    Each term is reduced to a crude stem and searched as a prefix, so
    "builds" finds "building" and "builder". Terms come from splitting on
    non-alphanumerics, so none can carry a Lucene metacharacter.
    """
    stems: list[str] = []
    for term in _TERM_SPLIT.split(q.lower()):
        if len(term) < 3 or term in _STOP_WORDS:
            continue
        stem = _stem(term)
        if stem not in stems:
            stems.append(stem)
    if not stems:
        return None
    return " ".join(f"{stem}*" for stem in stems[:MAX_TERMS])
