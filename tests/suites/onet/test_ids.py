"""Unit tests for O*NET identity helpers (no Neo4j)."""

import pytest

from ta_taxonomies.suites.onet.ids import (
    OnetIdError,
    software_slug,
    suite_id_element,
    suite_id_job_zone,
    suite_id_occupation,
    suite_id_scale,
    suite_id_software,
    suite_id_task,
)


def test_occupation_id_keeps_soc_string() -> None:
    assert suite_id_occupation("15-1252.00") == "onet:occupation:15-1252.00"
    assert suite_id_occupation("11-1011.00") == "onet:occupation:11-1011.00"


def test_occupation_id_rejects_empty() -> None:
    with pytest.raises(OnetIdError):
        suite_id_occupation("")
    with pytest.raises(OnetIdError):
        suite_id_occupation("   ")


def test_element_id_is_content_model_code() -> None:
    assert suite_id_element("2.B.3.e") == "onet:element:2.B.3.e"
    assert suite_id_element("1.A.1.a") == "onet:element:1.A.1.a"
    assert suite_id_element("4.A.2.b.1") == "onet:element:4.A.2.b.1"


def test_task_and_zone_and_scale_ids() -> None:
    assert suite_id_task("8823") == "onet:task:8823"
    assert suite_id_job_zone("3") == "onet:job-zone:3"
    assert suite_id_scale("IM") == "onet:scale:IM"


def test_software_slug_is_stable_and_casefold() -> None:
    assert software_slug("Adobe Acrobat") == "adobe-acrobat"
    assert software_slug("Adobe  Acrobat") == "adobe-acrobat"
    assert suite_id_software("Python") == "onet:software:python"
    assert software_slug("C") != software_slug("C#")
    assert software_slug("C#") == "c-sharp"
    assert software_slug("C++") == "c-plus-plus"


def test_software_slug_rejects_empty() -> None:
    with pytest.raises(OnetIdError):
        software_slug("***")
