"""O*NET suite package: US occupations, descriptors, and tasks.

Use case: one place for the O*NET taxonomy in Talent Angels — load the graph
(``load`` module) and query it (``OnetSuite`` in tools). Public export is
``OnetSuite`` for TA-agents and scripts.

Domain notes: identity is a *code*, not a URI (``15-1252.00``, ``2.B.3.e``) and
codes stay strings. Occupation↔descriptor edges carry real Importance and
Level ratings with sample sizes and 95% confidence bounds, which is why
``score_paths`` is implemented here and stubbed in ESCO. The essential /
optional flag on those edges is a **declared projection** of Importance for
compatibility with ESCO-shaped callers, never something O*NET published.
See NOTES.md.
"""

from ta_taxonomies.suites.onet.tools import OnetSuite

__all__ = ["OnetSuite"]
