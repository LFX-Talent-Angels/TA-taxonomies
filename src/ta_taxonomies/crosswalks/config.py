"""Graph constants for the crosswalk layer (no I/O).

Use case: one place for the labels and relationship types the crosswalk loader
and queries share, so a rename is one edit.

Why the two relationship types are different strings, and not one type with a
``kind`` property: a Cypher query that forgets a ``WHERE r.kind = 'published'``
filter still returns rows. It just returns the wrong ones, silently, mixed in
with the right ones. Separating them at the relationship type means the
mistake is impossible to make by omission -- a query that wants published data
names ``CORRESPONDS_TO`` and physically cannot traverse project claims.
"""

from __future__ import annotations

SOURCE = "crosswalks"

# Provenance nodes. Every correspondence edge is attributed to one of these,
# so the citation lives once and edges stay light.
LABEL_CROSSWALK_SOURCE = "CrosswalkSource"

# Recorded absences (ARCHITECTURE.md: "no link" is an answer, not a gap).
LABEL_NO_LINK = "NoLink"

# Published correspondence: transcribed from someone else's table.
REL_CORRESPONDS_TO = "CORRESPONDS_TO"

# This project's own claim. Deliberately NOT reachable from a query written
# for published data.
REL_ASSERTED_CORRESPONDS_TO = "ASSERTED_CORRESPONDS_TO"

# Edge -> provenance node.
REL_ATTRIBUTED_TO = "ATTRIBUTED_TO"

# Source node -> recorded absence. An absence hangs off the node it is about,
# so "which ESCO occupations have no O*NET counterpart" is a one-hop query.
REL_RECORDED_NO_LINK = "RECORDED_NO_LINK"

# Relationship types a caller may traverse across suites. Claims are excluded
# by default: opting into project opinion should be an explicit act.
TRAVERSABLE_RELS: frozenset[str] = frozenset({REL_CORRESPONDS_TO})

BATCH = 500
