"""Explicit cross-taxonomy links. Nothing cross-suite lives anywhere else.

Occupation hub: SOC/ISCO (BLS native; O*NET-SOC prefixes; ESCO via ISCO-08 --
its SOC bridge is indirect and lossy, and says so). SFIA bridges
skill-to-skill. No reliable link => "no link", recorded.

Every correspondence here is one of exactly two kinds, and the type system
keeps them apart:

- ``PublishedCorrespondence`` -- transcribed from a table a named authority
  published. Requires ``Provenance``: publisher, URL, pinned version,
  retrieval date, licence, and the method the publisher used.
- ``AssertedCorrespondence`` -- this project's own claim. Requires an owner, a
  written rationale, and a lifecycle status (observed -> proposed -> reviewed
  -> accepted -> deprecated). Nothing populates it yet.

They are also separate relationship types in the graph (``CORRESPONDS_TO`` vs
``ASSERTED_CORRESPONDS_TO``), so a query written for published data cannot
traverse project opinion by forgetting a filter.

What is loaded today: the ESCO <-> O*NET-SOC 2019 occupation crosswalk. There
is no published skills crosswalk between ESCO and O*NET, and per ADR-0006
decision 4 this repo does not manufacture one. See ``README.md`` in this
package for the evidence and for the options open to the team.
"""

from ta_taxonomies.crosswalks.models import (
    CLAIM_TRANSITIONS,
    AssertedCorrespondence,
    ClaimStatus,
    Correspondence,
    MappingMethod,
    MatchStrength,
    NoLink,
    Provenance,
    PublishedCorrespondence,
    can_transition,
)
from ta_taxonomies.crosswalks.sources import SOURCES, get_source
from ta_taxonomies.crosswalks.tools import Crosswalks

__all__ = [
    "CLAIM_TRANSITIONS",
    "SOURCES",
    "AssertedCorrespondence",
    "ClaimStatus",
    "Correspondence",
    "Crosswalks",
    "MappingMethod",
    "MatchStrength",
    "NoLink",
    "Provenance",
    "PublishedCorrespondence",
    "can_transition",
    "get_source",
]
