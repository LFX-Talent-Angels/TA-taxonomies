"""JobTech fetch: pinned downloads, GraphQL paging and the document build (no network)."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from ta_taxonomies.suites.jobtech import fetch
from ta_taxonomies.suites.jobtech.fetch import JobtechFetchError, build_document

ESCO = "http://data.europa.eu/esco"


def _c(cid: str, ctype: str, label: str = "", **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": cid,
        "type": ctype,
        "preferred_label": label or cid,
        "alternative_labels": [],
        "hidden_labels": [],
        "definition": f"def {cid}",
        "esco_uri": None,
        "ssyk_code_2012": None,
        "broader": [],
        "related": [],
    }
    for kind in ("exact", "close", "broad", "narrow"):
        base[f"{kind}_match"] = []
    base.update(fields)
    return base


def _ref(*ids: str) -> list[dict[str, str]]:
    return [{"id": i} for i in ids]


def common() -> dict[str, Any]:
    concepts = [
        _c("SSY1_aaa_001", "ssyk-level-1", "Service", ssyk_code_2012="7"),
        _c(
            "SSY2_aaa_002",
            "ssyk-level-2",
            "Livsmedel",
            ssyk_code_2012="76",
            broader=_ref("SSY1_aaa_001"),
        ),
        _c(
            "SSY3_aaa_003",
            "ssyk-level-3",
            "Bagare m.fl.",
            ssyk_code_2012="761",
            broader=_ref("SSY2_aaa_002"),
        ),
        _c(
            "SSY4_aaa_004",
            "ssyk-level-4",
            "Bagare och konditorer",
            ssyk_code_2012="7612",
            broader=_ref("SSY3_aaa_003", "FLD0_aaa_000"),
        ),
        _c("FLD0_aaa_000", "occupation-field", "Hantverk"),
        _c("ISC0_aaa_000", "isco-level-4", "Bakers", esco_uri=f"{ESCO}/isco/C7512"),
        _c("ESO1_aaa_001", "esco-occupation", "bagare", esco_uri=f"{ESCO}/occupation/baker"),
        _c("ESO2_aaa_002", "esco-occupation", "konditor", esco_uri=f"{ESCO}/occupation/pastry"),
        _c("ESS1_aaa_001", "esco-skill", "baka", esco_uri=f"{ESCO}/skill/bake"),
        _c(
            "OCC1_aaa_001",
            "occupation-name",
            "Bagare/Konditor",
            alternative_labels=["Bagare", "Bagare/Konditor"],
            broader=_ref("SSY4_aaa_004", "ISC0_aaa_000"),
            exact_match=_ref("ESO1_aaa_001", "ESO2_aaa_002"),
        ),
        _c(
            "OCC2_aaa_002",
            "occupation-name",
            "Konditor",
            broader=_ref("SSY4_aaa_004"),
            narrow_match=_ref("ESO2_aaa_002"),
        ),
        _c("HED1_aaa_001", "skill-headline", "Livsmedel"),
        _c(
            "SKL1_aaa_001",
            "skill",
            "Matbröd",
            broader=_ref("HED1_aaa_001"),
            related=_ref("SSY4_aaa_004"),
            close_match=_ref("ESS1_aaa_001"),
        ),
        _c("SKL2_aaa_002", "skill", "Konfekt", broader=_ref("HED1_aaa_001")),
    ]
    return {"data": {"concepts": concepts}}


SUBST = {
    "data": {
        "concepts": [
            {
                "id": "OCC1_aaa_001",
                "substitutes": [{"id": "OCC2_aaa_002", "substitutability_percentage": 75}],
                "substituted_by": [],
            },
            {"id": "OCC2_aaa_002", "substitutes": [], "substituted_by": []},
        ]
    }
}
DIRECT = {
    "version": 31,
    "data": {
        "concepts": [
            {
                "id": "OCC1_aaa_001",
                "essential": [{"id": "SKL1_aaa_001", "type": "skill"}],
                "optional": [
                    {"id": "SKL1_aaa_001", "type": "skill"},
                    {"id": "SKL2_aaa_002", "type": "skill"},
                    {"id": "JOB0_aaa_000", "type": "job-title"},
                ],
            }
        ]
    },
}


def test_document_shape() -> None:
    doc = build_document(common(), SUBST, DIRECT)
    assert [(g["code"], g["level"], g["parent"]) for g in doc["ssyk_groups"]] == [
        ("7", 1, None),
        ("76", 2, "7"),
        ("761", 3, "76"),
        ("7612", 4, "761"),
    ]
    occ = {o["concept_id"]: o for o in doc["occupations"]}
    baker = occ["OCC1_aaa_001"]
    assert baker["ssyk_code"] == "7612"
    assert baker["isco_codes"] == ["7512"]
    assert baker["alt_labels"] == ["Bagare"]  # the label itself is not an alias
    assert baker["esco"] == {
        "exact": [f"{ESCO}/occupation/baker", f"{ESCO}/occupation/pastry"],
        "close": [],
        "broad": [],
        "narrow": [],
    }
    # Direct skills: essential wins over optional; non-skill concepts are dropped.
    assert baker["essential"] == ["SKL1_aaa_001"]
    assert baker["optional"] == ["SKL2_aaa_002"]
    assert occ["OCC2_aaa_002"]["essential"] == occ["OCC2_aaa_002"]["optional"] == []
    assert occ["OCC2_aaa_002"]["esco"]["narrow"] == [f"{ESCO}/occupation/pastry"]

    skills = {s["concept_id"]: s for s in doc["skills"]}
    assert skills["SKL1_aaa_001"]["ssyk_codes"] == ["7612"]
    assert skills["SKL1_aaa_001"]["headline"] == "HED1_aaa_001"
    assert skills["SKL1_aaa_001"]["esco"]["close"] == [f"{ESCO}/skill/bake"]
    assert doc["headlines"] == [{"concept_id": "HED1_aaa_001", "label": "Livsmedel"}]
    assert doc["substitutions"] == [{"from": "OCC1_aaa_001", "to": "OCC2_aaa_002", "pct": 75}]
    assert doc["meta"]["direct_skills"] is True


def test_without_direct_skills() -> None:
    doc = build_document(common(), SUBST, None)
    assert doc["meta"]["direct_skills"] is False
    assert all(not o["essential"] and not o["optional"] for o in doc["occupations"])


def test_an_occupation_needs_one_unit_group() -> None:
    bad = common()
    for c in bad["data"]["concepts"]:
        if c["id"] == "OCC2_aaa_002":
            c["broader"] = []
    with pytest.raises(JobtechFetchError, match="OCC2_aaa_002"):
        build_document(bad, SUBST, None)


def test_ssyk_codes_must_fit_their_level_and_parent() -> None:
    bad = common()
    bad["data"]["concepts"][3]["ssyk_code_2012"] = "7699"
    with pytest.raises(JobtechFetchError, match="7699"):
        build_document(bad, SUBST, None)


def test_a_file_without_concepts_is_refused() -> None:
    with pytest.raises(JobtechFetchError, match="data.concepts"):
        build_document({"oops": 1}, SUBST, None)


# -- download --------------------------------------------------------------------


def _pin(monkeypatch: pytest.MonkeyPatch, payloads: dict[str, bytes]) -> None:
    files = tuple(
        fetch.BulkFile(name, hashlib.sha256(data).hexdigest()) for name, data in payloads.items()
    )
    monkeypatch.setattr(fetch, "BULK_FILES", files)


def test_bulk_download_checks_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = {"a": b'{"data": 1}', "b": b'{"data": 2}'}
    _pin(monkeypatch, payloads)
    got: list[str] = []

    def get(url: str) -> bytes:
        got.append(url)
        return payloads[url.rsplit("/", 1)[1].removesuffix(".json")]

    fetch.download_bulk(tmp_path, get=get, pause=lambda _s: None)
    assert got == [f"{fetch.BULK_BASE}/a/a.json", f"{fetch.BULK_BASE}/b/b.json"]
    got.clear()
    fetch.download_bulk(tmp_path, get=get, pause=lambda _s: None)
    assert got == []


def test_bulk_download_refuses_html_and_new_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin(monkeypatch, {"a": b'{"data": 1}'})
    with pytest.raises(JobtechFetchError, match="JSON"):
        fetch.download_bulk(tmp_path, get=lambda u: b"<html>", pause=lambda _s: None)
    with pytest.raises(JobtechFetchError, match="changed"):
        fetch.download_bulk(tmp_path, get=lambda u: b'{"new": 1}', pause=lambda _s: None)


def test_direct_skills_are_paged_and_pinned_to_the_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "PAGE", 2)
    pages = [
        [
            {"id": "B", "essential": [], "optional": []},
            {"id": "A", "essential": [], "optional": []},
        ],
        [{"id": "C", "essential": [], "optional": []}],
    ]
    queries: list[str] = []

    def get(url: str) -> bytes:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
        queries.append(query)
        return json.dumps({"data": {"concepts": pages[len(queries) - 1]}}).encode()

    path = fetch.download_direct_skills(tmp_path, get=get, pause=lambda _s: None)
    assert all('version: "31"' in q for q in queries)
    assert ["offset: 0" in queries[0], "offset: 2" in queries[1]] == [True, True]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 31
    assert [c["id"] for c in saved["data"]["concepts"]] == ["A", "B", "C"]


def test_graphql_errors_stop_the_fetch(tmp_path: Path) -> None:
    with pytest.raises(JobtechFetchError, match="GraphQL"):
        fetch.download_direct_skills(
            tmp_path, get=lambda u: b'{"errors": [{"message": "boom"}]}', pause=lambda _s: None
        )
