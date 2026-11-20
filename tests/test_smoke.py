"""Smoke tests — confirm the package and its skeleton import cleanly."""

import ta_taxonomies


def test_version() -> None:
    assert ta_taxonomies.__version__


def test_suites_declared() -> None:
    assert set(ta_taxonomies.SUITES) == {"esco", "onet", "sfia", "bls", "jobtech"}


def test_packages_import() -> None:
    import ta_taxonomies.contract  # noqa: F401
    import ta_taxonomies.crosswalks  # noqa: F401
    import ta_taxonomies.ingestion  # noqa: F401
    import ta_taxonomies.suites.bls  # noqa: F401
    import ta_taxonomies.suites.esco  # noqa: F401
    import ta_taxonomies.suites.jobtech  # noqa: F401
    import ta_taxonomies.suites.onet  # noqa: F401
    import ta_taxonomies.suites.sfia  # noqa: F401
