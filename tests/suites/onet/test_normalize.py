"""Fixture normalize path without Neo4j."""

from ta_taxonomies.suites.onet.ids import suite_id_occupation
from ta_taxonomies.suites.onet.load import load_fixture_document, normalize_document


def test_fixture_normalize_shapes() -> None:
    payload = normalize_document(load_fixture_document())

    assert len(payload["occupations"]) == 4
    assert any(o["code"] == "15-1252.00" for o in payload["occupations"])
    software_dev = next(o for o in payload["occupations"] if o["code"] == "15-1252.00")
    assert software_dev["id"] == suite_id_occupation("15-1252.00")
    assert software_dev["source"] == "onet"
    assert "Software Engineer" in software_dev["alt_labels"]
    assert software_dev["pref_label"] == "Software Developers"

    assert payload["has_skill"]
    assert {e["relation_type"] for e in payload["has_skill"]} <= {"essential", "transferable"}
    programming = [
        e
        for e in payload["has_skill"]
        if e["to_id"] == "onet:element:2.B.3.e" and e["from_id"] == software_dev["id"]
    ]
    assert programming
    assert programming[0]["importance"] == 4.0

    assert payload["software"]
    assert payload["uses_software"]
    assert payload["tasks"]
    assert payload["performs_task"]
    assert all(
        edge["from_id"].startswith("onet:") and edge["to_id"].startswith("onet:")
        for key in ("has_skill", "related_to", "performs_task")
        for edge in payload[key]
    )


def test_related_occupations_only_keep_loaded_endpoints() -> None:
    payload = normalize_document(load_fixture_document())
    occupation_ids = {row["id"] for row in payload["occupations"]}
    for edge in payload["related_to"]:
        assert edge["from_id"] in occupation_ids
        assert edge["to_id"] in occupation_ids
