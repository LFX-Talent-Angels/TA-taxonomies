"""Talent Angels taxonomy suites.

One suite per taxonomy — loader + graph schema + tools — behind the shared
suite contract (see ARCHITECTURE.md and TA-workspace ADR-0003/0004):

    search_nodes · get_neighbors · enumerate_paths · score_paths

Suites: esco, onet, sfia, bls, and later jobtech (Sweden JobTech — ADR-0006).
The assistant runtime (TA-agents) consumes this package as a versioned
library and imports only the contract surface.
"""

__version__ = "0.1.0"

#: Suites in scope. jobtech (Sweden JobTech, ADR-0006) joins after these four.
SUITES = ("esco", "onet", "sfia", "bls")
