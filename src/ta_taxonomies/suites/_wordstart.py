"""Word-start matching for the Locate contains tier, shared by every suite.

A plain substring match finds a query inside other words: "swe" matched
"Answering" and "Sweater", "ai" matched "repairer" and "tailor". A label only
matches when the query starts a word in it ("data scien" still finds "data
science", "engineer" still finds "software engineer").
"""

from __future__ import annotations

import re

_ACRONYM = re.compile(r"[A-Z]{2,4}")


def word_start_pattern(q: str) -> str:
    """A Cypher (Java) regex that is true when ``q`` starts a word in a label.

    Case-insensitive, Unicode-aware. Every character that is not a letter, a
    digit or a space is escaped, which is always literal in Java regex, so the
    query can never add regex syntax of its own.
    """
    escaped = "".join(c if c.isalnum() or c == " " else "\\" + c for c in q)
    # No boundary check when the query itself starts with punctuation (".net").
    boundary = r"(?<![\p{L}\p{N}])" if q[:1].isalnum() else ""
    if _ACRONYM.fullmatch(q.strip()):
        # An acronym alone is that acronym: a whole word, in capitals. "AI" is
        # not the start of "aircraft", and "IT" is not the word "it".
        return "(?su).*" + boundary + escaped + r"(?![\p{L}\p{N}])" + ".*"
    return "(?isu).*" + boundary + escaped + ".*"
