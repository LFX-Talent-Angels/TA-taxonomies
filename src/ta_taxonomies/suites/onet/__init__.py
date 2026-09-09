"""O*NET suite (US DoL occupations, tasks, skills, software).

Pinned source is O*NET **31.0**. Load the graph (``load``) and query it
(``OnetSuite``). See ``INVENTORY.md`` and ``NOTES.md``.
"""

from ta_taxonomies.suites.onet.tools import OnetSuite

__all__ = ["OnetSuite"]
