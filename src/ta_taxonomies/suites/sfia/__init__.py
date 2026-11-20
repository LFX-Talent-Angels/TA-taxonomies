"""SFIA suite package: digital skills, responsibility levels, and graph ops.

Use case: one place for the SFIA 9 taxonomy in Talent Angels — load the graph
(``load`` module, fed by ``fetch``) and query it (``SfiaSuite`` in tools).
Public export is ``SfiaSuite`` for TA-agents and scripts.

Notes from the team's Sprint 1 slice: no occupations — SFIA crosswalks
skill-to-skill, not occupation-to-occupation; levels are first-class nodes
(level 4 != level 7 of the same skill); licensing forbids republishing SFIA
prose — store codes and pointers only (``sfia_ref`` citations, ``url`` links
to sfia-online.org), fetch the payload at runtime. SFIA's skill code ``ISCO``
is unrelated to the ISCO occupation classification; ``source`` + ``source_id``
keep them apart. See README.md for the license and the graph model.
"""

from ta_taxonomies.suites.sfia.tools import SfiaSuite

__all__ = ["SfiaSuite"]
