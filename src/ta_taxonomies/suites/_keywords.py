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
    occupation occupations career careers role roles position positions
    profession professions title titles field
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


#: The word after one of these is ruled out ("not coding"), not searched for.
_NEGATIONS = frozenset({"not", "no", "without", "except", "never", "nor"})


def topic_stems(q: str) -> list[str]:
    """The crude stems of the topic words in ``q``, in order, at most MAX_TERMS."""
    stems: list[str] = []
    previous = ""
    for term in _TERM_SPLIT.split(q.lower()):
        ruled_out = previous in _NEGATIONS
        previous = term
        if ruled_out or len(term) < 3 or term in _STOP_WORDS or term in _NEGATIONS:
            continue
        stem = _stem(term)
        if stem not in stems:
            stems.append(stem)
    return stems[:MAX_TERMS]


def keyword_query(q: str) -> str | None:
    """An OR-of-prefixes Lucene query for the topic words in ``q``, or None.

    Each term is reduced to a crude stem and searched as a prefix, so
    "builds" finds "building" and "builder". Terms come from splitting on
    non-alphanumerics, so none can carry a Lucene metacharacter.
    """
    stems = topic_stems(q)
    if not stems:
        return None
    if len(stems) == 1 and _drops_a_short_word(q):
        # "IT manager": "IT" is too short to search, so the list would be every
        # "... manager" title. The vector search answers alone instead.
        return None
    return " ".join(f"{stem}*" for stem in stems)


def _drops_a_short_word(q: str) -> bool:
    """A short word that carries meaning ("IT", "HR", "UX") was left out.

    Read in the original case: "IT" in capitals is an acronym, "it" a pronoun.
    """
    for term in _TERM_SPLIT.split(q):
        if not 0 < len(term) < 3:
            continue
        if term.isupper() and len(term) == 2:
            return True
        if term.lower() not in _STOP_WORDS and term.lower() not in _NEGATIONS:
            return True
    return False


def enough_words(stems: list[str], names: list[str]) -> bool:
    """A keyword hit must share two topic words with a query that has two or more.

    One stray word is not a match: "xyzzy-nonexistent-occupation" found every
    "occupational ..." title through a single word. One-word queries need one.
    """
    words = {word for name in names for word in _TERM_SPLIT.split(name.lower()) if word}
    shared = sum(1 for stem in stems if any(word.startswith(stem) for word in words))
    return shared >= min(2, len(stems))
