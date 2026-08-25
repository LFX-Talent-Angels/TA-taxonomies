"""BLS/SOC suite — the occupation spine and employment projections.

ADR-0006 adopts BLS for exactly this: *"the occupation spine and employment
projections"*. It is US public domain, it downloads without registration, and
**it has no skills layer** — which is a property of the source, not a gap in
this package. Asking this suite what skills an occupation needs is asking the
wrong suite; ask O*NET or ESCO and come back through a crosswalk on the SOC
code.

What lives here that lives nowhere else:

* the **SOC spine** — all four levels as first-class nodes with BLS's own
  titles and definitions, the hub every crosswalk in ADR-0006 decision 4 ends
  on (``O*NET-SOC → SOC``, ``BLS native SOC``, ``ISCO ↔ SOC``);
* **employment, wages and ten-year projections** per occupation, and the same
  figures across 420 industries;
* ``BlsSuite.resolve_soc`` — take a SOC code from any other suite's identifier
  and land on the authoritative node with its full roll-up, or get a recorded
  "no link".

Entry points::

    python -m ta_taxonomies.suites.bls.fetch --out data/bls/raw
    python -m ta_taxonomies.suites.bls.load  --mode fixture

See ``NOTES.md`` for what was measured, ``README.md`` for source terms.
"""
