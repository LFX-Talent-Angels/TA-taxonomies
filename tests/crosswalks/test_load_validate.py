"""End-to-end load against a live Neo4j, skipped when none is configured.

Mirrors the suite loaders' load-validation tests: run the fixture through the
real pipeline, then assert the graph holds what the loader said it wrote.

Point this at a scratch database. It writes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from neo4j import Driver, GraphDatabase

from ta_taxonomies.crosswalks.config import LABEL_NO_LINK, REL_CORRESPONDS_TO
from ta_taxonomies.crosswalks.load import (
    CrosswalkLoadError,
    load_crosswalk,
    merge_correspondences,
)
from ta_taxonomies.crosswalks.models import PublishedCorrespondence
from ta_taxonomies.crosswalks.sources import ESCO_ONET_2019
from ta_taxonomies.crosswalks.tools import Crosswalks

SOURCE_KEY = "esco_onet_2019"
LIEUTENANT = "esco:occupation:b223f402-0318-4e27-8691-df6e7f7295d2"
AIR_FORCE_OFFICER = "esco:occupation:f2cc5978-e45c-4f28-b859-7f89221b0505"
SOFTWARE_DEVELOPER = "esco:occupation:f2b15a0e-e65a-438a-affb-29b9d50b77d1"


@pytest.fixture(scope="module")
def driver() -> Iterator[Driver]:
    uri = os.getenv("CROSSWALK_TEST_NEO4J_URI")
    password = os.getenv("CROSSWALK_TEST_NEO4J_PASSWORD")
    if not uri or not password:
        pytest.skip(
            "set CROSSWALK_TEST_NEO4J_URI and CROSSWALK_TEST_NEO4J_PASSWORD "
            "to run crosswalk load-validation against a scratch database"
        )
    user = os.getenv("CROSSWALK_TEST_NEO4J_USER", "neo4j")
    connection = GraphDatabase.driver(uri, auth=(user, password))
    try:
        connection.verify_connectivity()
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope="module")
def report(driver: Driver) -> dict:
    return load_crosswalk(driver, mode="fixture", source_key=SOURCE_KEY)


class TestFixtureLoad:
    def test_counts_survive_translation(self, report: dict) -> None:
        assert report["written"] is True
        assert report["counts"]["correspondences"] == report["resolved_correspondences"] == 8
        assert report["counts"]["no_links"] == report["recorded_no_links"] == 1

    def test_version_seam_is_reported_not_swallowed(self, report: dict) -> None:
        """A code the table has and ESCO does not is a fact, not a silent drop."""
        assert report["unresolved_esco_codes"] == 1
        assert report["unresolved_sample"] == ["9999.9"]

    def test_reload_is_idempotent(self, driver: Driver, report: dict) -> None:
        second = load_crosswalk(driver, mode="fixture", source_key=SOURCE_KEY)
        assert second["reset"]["correspondences_removed"] == 8
        assert second["counts"] == report["counts"]

    def test_no_dangling_edges(self, driver: Driver, report: dict) -> None:
        with driver.session() as session:
            record = session.run(
                f"MATCH ()-[r:{REL_CORRESPONDS_TO}]->() "
                "WHERE startNode(r).id IS NULL OR endNode(r).id IS NULL "
                "RETURN count(r) AS c"
            ).single()
        assert record is not None and record["c"] == 0


class TestQueriesAfterLoad:
    def test_forward_lookup(self, driver: Driver, report: dict) -> None:
        result = Crosswalks(driver).counterparts(SOFTWARE_DEVELOPER, to_suite="onet")
        assert {node.id for node in result.nodes} == {
            "onet:occupation:15-1252.00",
            "onet:occupation:15-1251.00",
            "onet:occupation:15-1253.00",
        }

    def test_reverse_lookup_works_without_knowing_file_direction(
        self, driver: Driver, report: dict
    ) -> None:
        result = Crosswalks(driver).counterparts("onet:occupation:15-1252.00", to_suite="esco")
        assert SOFTWARE_DEVELOPER in {node.id for node in result.nodes}

    def test_every_answer_carries_its_citation(self, driver: Driver, report: dict) -> None:
        result = Crosswalks(driver).counterparts(SOFTWARE_DEVELOPER, to_suite="onet")
        assert result.evidence
        assert all(SOURCE_KEY in pointer for pointer in result.evidence)
        assert any("model-assisted" in warning for warning in result.warnings)

    def test_recorded_absence_is_not_the_same_as_not_found(
        self, driver: Driver, report: dict
    ) -> None:
        """The trailing-zero pair, end to end and in the graph."""
        absent = Crosswalks(driver).counterparts(AIR_FORCE_OFFICER, to_suite="onet")
        assert absent.nodes == []
        assert any("no_link" in warning for warning in absent.warnings)
        assert "not_found" not in absent.warnings

        unknown = Crosswalks(driver).counterparts("esco:occupation:no-such-node")
        assert unknown.warnings == ["not_found"]

        linked = Crosswalks(driver).counterparts(LIEUTENANT, to_suite="onet")
        assert len(linked.nodes) == 2, "0110.10's links must not leak onto 0110.1"

    def test_coverage_reports_the_denominator(self, driver: Driver, report: dict) -> None:
        coverage = Crosswalks(driver).coverage(SOURCE_KEY)
        assert coverage["loaded"] is True
        assert coverage["correspondences"] == 8
        assert coverage["recorded_no_links"] == 1
        assert coverage["method"] == "model_assisted_validated"

    def test_unloaded_source_reports_itself_as_unloaded(self, driver: Driver) -> None:
        assert Crosswalks(driver).coverage("nope")["loaded"] is False


class TestEndpointsAreNeverInvented:
    def test_missing_target_is_detected_and_named(self, driver: Driver, report: dict) -> None:
        """A missing O*NET node is reported, never created.

        Checked in dry-run because ``--mode fixture`` deliberately seeds its own
        stand-in endpoints (CI has no loaded suites), which would paper over the
        gap before the check ran. Dry-run skips that seeding, so this exercises
        the same detection ``--mode full`` relies on.

        The property also holds on real data: a full dry-run of the published
        table against the complete ESCO graph reports all 957 referenced O*NET
        occupations as missing while the O*NET suite is unloaded, and the loader
        refuses to write rather than creating them.
        """
        with driver.session() as session:
            session.run("MATCH (n {id: 'onet:occupation:15-1252.00'}) DETACH DELETE n")
        try:
            dry = load_crosswalk(driver, mode="fixture", source_key=SOURCE_KEY, dry_run=True)
            assert dry["onet_targets_missing"] == 1
            assert dry["onet_targets_missing_sample"] == ["onet:occupation:15-1252.00"]
            assert dry["written"] is False

            with driver.session() as session:
                record = session.run(
                    "MATCH (n {id: 'onet:occupation:15-1252.00'}) RETURN count(n) AS c"
                ).single()
            assert record is not None and record["c"] == 0, "dry-run created the endpoint"
        finally:
            load_crosswalk(driver, mode="fixture", source_key=SOURCE_KEY)

    def test_merge_refuses_rows_whose_endpoints_are_absent(self, driver: Driver) -> None:
        """The write path fails loudly rather than silently dropping the row.

        Silently dropping would be the dangerous failure: the row would vanish
        and look indistinguishable from a genuine absence of correspondence.
        """
        orphan = PublishedCorrespondence(
            from_id=SOFTWARE_DEVELOPER,
            to_id="onet:occupation:99-9999.99",
            from_suite="esco",
            to_suite="onet",
            provenance=ESCO_ONET_2019,
        )
        with driver.session() as session:
            with pytest.raises(CrosswalkLoadError, match="never creates endpoints"):
                merge_correspondences(session, [orphan])
            record = session.run(
                "MATCH (n {id: 'onet:occupation:99-9999.99'}) RETURN count(n) AS c"
            ).single()
        assert record is not None and record["c"] == 0


class TestDryRunIsReadOnly:
    def test_dry_run_writes_nothing(self, driver: Driver) -> None:
        """The coverage report must be safe to point at someone else's graph."""
        with driver.session() as session:
            session.run(f"MATCH ()-[r:{REL_CORRESPONDS_TO}]->() DELETE r")
            session.run(f"MATCH (n:{LABEL_NO_LINK}) DETACH DELETE n")

        dry = load_crosswalk(driver, mode="fixture", source_key=SOURCE_KEY, dry_run=True)
        assert dry["written"] is False
        assert dry["resolved_correspondences"] == 8

        with driver.session() as session:
            record = session.run(
                f"MATCH ()-[r:{REL_CORRESPONDS_TO}]->() RETURN count(r) AS c"
            ).single()
        assert record is not None and record["c"] == 0
