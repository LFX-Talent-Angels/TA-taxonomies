"""BLS suite package: US occupations (SOC 2018) with wage, outlook and skills.

Use case: one place for the BLS data in Talent Angels — the SOC 2018
occupations and groups, with Employment Projections (2025-35) facts and the 17
EP skills on each occupation. ``fetch`` builds one JSON document from the BLS
files, ``load`` puts it in Neo4j, and the suite tools query it.

SOC-native, like O*NET: the SOC code is a property, never a shared node, so a
crosswalk joins the two suites on the code and nothing else. Public domain
(BLS), with the EP skill scores credited to O*NET. See NOTES.md.
"""

from ta_taxonomies.suites.bls.tools import BlsSuite

__all__ = ["BlsSuite"]
