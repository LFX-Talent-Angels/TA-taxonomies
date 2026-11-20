"""search_nodes / get_neighbors contract tests against the SFIA fixture in Neo4j.

Skipped when Neo4j is not reachable (CI without Docker).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from neo4j.exceptions import ServiceUnavailable

from ta_taxonomies.suites.sfia.db import neo4j_driver, verify_connectivity
from ta_taxonomies.suites.sfia.load import run_load
from ta_taxonomies.suites.sfia.tools import SfiaSuite


def _neo4j_available() -> bool:
    import os

    if not os.getenv("NEO4J_PASSWORD"):
        return False
    try:
        with neo4j_driver() as (driver, _db):
            verify_connectivity(driver)
        return True
    except (ServiceUnavailable, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _neo4j_available(),
    reason="Neo4j not available (start with: docker compose up -d)",
)


@pytest.fixture(scope="module")
def loaded_suite() -> Iterator[SfiaSuite]:
    """Load fixture once; provide suite with a live driver for the module."""
    from neo4j import GraphDatabase

    from ta_taxonomies.suites.sfia.db import neo4j_config_from_env

    run_load(mode="fixture", wipe=True)
    cfg = neo4j_config_from_env()
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
    suite = SfiaSuite(driver, database=cfg["database"])
    yield suite
    driver.close()


def test_search_exact_pref_functional_testing(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.search_nodes("Functional testing")
    assert not any(w == "not_found" for w in result.warnings)
    top = result.candidates[0]
    assert top.method == "exact_pref"
    assert top.confidence >= 0.95
    assert top.node.id == "sfia:skill:TEST"
    assert top.node.source == "sfia"
    assert top.node.source_id == "TEST"
    assert top.node.properties["sfia_ref"] == "SFIA 9 · TEST"
    assert top.node.properties["url"].startswith("https://sfia-online.org/")
    assert "description" not in top.node.properties


def test_search_casefold(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.search_nodes("functional TESTING")
    assert [c.method for c in result.candidates] == ["casefold_pref"]
    assert result.candidates[0].node.id == "sfia:skill:TEST"


def test_search_by_skill_code(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.search_nodes("PROG")
    assert [(c.method, c.node.id) for c in result.candidates] == [("exact_code", "sfia:skill:PROG")]


def test_isco_is_the_sfia_skill(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.search_nodes("ISCO")
    assert [c.node.label for c in result.candidates] == ["Information systems coordination"]


def test_search_by_level_code(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.search_nodes("PROG-4")
    assert [c.node.id for c in result.candidates] == ["sfia:level:PROG:4"]
    assert result.candidates[0].node.properties["level"] == 4


def test_kind_filters(loaded_suite: SfiaSuite) -> None:
    cat = loaded_suite.search_nodes("Strategy and architecture", kind="category")
    assert [c.node.id for c in cat.candidates] == ["sfia:category:strategy-and-architecture"]

    sub = loaded_suite.search_nodes("Systems development", kind="subcategory")
    assert [c.node.id for c in sub.candidates] == ["sfia:subcategory:systems-development"]

    levels = loaded_suite.search_nodes("Functional testing", kind="level")
    assert levels.candidates
    assert all(c.node.kind == "SkillLevel" for c in levels.candidates)

    # Categories are not skills.
    none = loaded_suite.search_nodes("Strategy and architecture", kind="skill")
    assert all(c.node.kind == "Skill" for c in none.candidates)


def test_default_search_returns_skills_not_levels(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.search_nodes("information")
    assert result.candidates
    assert {c.node.kind for c in result.candidates} == {"Skill"}
    assert {c.method for c in result.candidates} == {"contains"}


def test_search_not_found(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.search_nodes("zzznosuchskill999")
    assert result.candidates == []
    assert "not_found" in result.warnings


def test_search_unknown_kind_returns_warning(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.search_nodes("Functional testing", kind="occupation")
    assert result.candidates == []
    assert result.warnings == ["unknown_kind:occupation"]


def test_levels_via_get_neighbors(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.get_neighbors("sfia:skill:PROG", ["HAS_LEVEL"])
    levels = sorted(n.properties["level"] for n in result.nodes if n.kind == "SkillLevel")
    assert levels == [2, 3, 4, 5, 6]
    assert {e.type for e in result.edges} == {"HAS_LEVEL"}
    assert all(e.from_id == "sfia:skill:PROG" for e in result.edges)


def test_neighbors_climb_the_tree(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.get_neighbors("sfia:skill:PROG", ["BROADER_THAN"])
    assert [e.to_id for e in result.edges] == ["sfia:subcategory:systems-development"]
    assert "node_not_found" in loaded_suite.get_neighbors("sfia:skill:NOPE").warnings


def test_paths_from_level_to_category(loaded_suite: SfiaSuite) -> None:
    result = loaded_suite.enumerate_paths(
        "sfia:level:PROG:4", "sfia:category:development-and-implementation"
    )
    assert result.paths
    assert result.paths[0].node_ids == [
        "sfia:level:PROG:4",
        "sfia:skill:PROG",
        "sfia:subcategory:systems-development",
        "sfia:category:development-and-implementation",
    ]
