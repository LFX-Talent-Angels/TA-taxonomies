"""SFIA suite package: professional skills and levels of responsibility.

Use case: one place for the SFIA framework in Talent Angels — load the graph
(``load`` module) and query it (``SfiaSuite`` in tools). Public export is
``SfiaSuite`` for TA-agents and scripts.

Domain notes, and the first one governs everything else:

* **Structure only, never SFIA's text.** SFIA's free licence covers personal
  and internal use; redistributing SFIA material needs a fee-bearing licence,
  and a public Apache-2.0 repository is redistribution to everyone
  (TA-workspace ADR-0006 §2). This suite stores skill codes, skill names, level
  numbers and structure. Descriptions are fetched at runtime by users holding
  their own SFIA access; every skill node carries the URL where its definition
  lives, which is what "pointer, not payload" means here.
* **No occupations.** SFIA cannot answer "what skills does job X need". What it
  has and no other adopted source does is a **responsibility axis**: seven
  ordered levels, and which of them each skill is defined at.
* **Bare codes collide.** SFIA's ``ISCO`` is *Information systems
  coordination*, unrelated to the ILO occupation classification ESCO aligns to.
  Ids are suite-scoped and every node carries ``source`` and ``source_id``.

See NOTES.md.
"""

from ta_taxonomies.suites.sfia.tools import SfiaSuite

__all__ = ["SfiaSuite"]
