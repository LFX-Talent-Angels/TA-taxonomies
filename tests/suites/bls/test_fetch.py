"""BLS fetch: User-Agent rules, pinned downloads and the row parsers (no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ta_taxonomies.suites.bls import fetch
from ta_taxonomies.suites.bls.fetch import (
    BlsFetchError,
    assemble,
    check_user_agent,
    parse_definitions,
    parse_direct_match_titles,
    parse_projections,
    parse_skill_crosswalk,
    parse_skill_scores,
    parse_structure,
)

TITLE_BLOCK = [("U.S. Bureau of Labor Statistics",), (None,), ("November 2017",)]


# -- User-Agent ----------------------------------------------------------------


def test_user_agent_must_name_a_contact() -> None:
    assert check_user_agent(" Loader/0.1 (a@b.org) ") == "Loader/0.1 (a@b.org)"
    for bad in (None, "", "   ", "Loader/0.1", "Loader/0.1 (team)"):
        with pytest.raises(BlsFetchError):
            check_user_agent(bad)


def test_user_agent_must_not_hold_a_url() -> None:
    # bls.gov's CDN refuses any User-Agent that contains a URL.
    for bad in ("Loader (+https://example.org; a@b.org)", "Loader (www.x.org a@b.org)"):
        with pytest.raises(BlsFetchError, match="URL"):
            check_user_agent(bad)


# -- download ------------------------------------------------------------------


def _pin_to(monkeypatch: pytest.MonkeyPatch, payloads: dict[str, bytes]) -> None:
    """Point SOURCE_FILES at fake payloads with matching pins."""
    files = tuple(
        fetch.SourceFile(
            name, f"https://www.bls.gov/x/{name}", hashlib.sha256(data).hexdigest(), "S"
        )
        for name, data in payloads.items()
    )
    monkeypatch.setattr(fetch, "SOURCE_FILES", files)


def test_download_checks_pins_and_skips_good_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads = {"a.xlsx": b"PK-a", "b.xlsx": b"PK-b"}
    _pin_to(monkeypatch, payloads)
    calls: list[tuple[str, str]] = []
    pauses: list[float] = []

    def get(url: str, ua: str) -> bytes:
        calls.append((url, ua))
        return payloads[url.rsplit("/", 1)[1]]

    hashes = fetch.download(tmp_path, "L/0.1 (a@b.org)", get=get, pause=pauses.append)
    assert sorted(hashes) == ["a.xlsx", "b.xlsx"]
    assert [ua for _, ua in calls] == ["L/0.1 (a@b.org)"] * 2
    assert pauses == [fetch.PAUSE_S]  # one pause between two downloads

    calls.clear()
    fetch.download(tmp_path, "L/0.1 (a@b.org)", get=get, pause=pauses.append)
    assert calls == []  # both files already match their pins


def test_download_refuses_an_html_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_to(monkeypatch, {"a.xlsx": b"PK-a"})
    with pytest.raises(BlsFetchError, match="spreadsheet"):
        fetch.download(
            tmp_path, "L (a@b.org)", get=lambda u, a: b"<html>Access Denied</html>", pause=print
        )
    assert not (tmp_path / "a.xlsx").exists()


def test_download_stops_on_a_new_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_to(monkeypatch, {"a.xlsx": b"PK-a"})
    with pytest.raises(BlsFetchError, match="new release"):
        fetch.download(tmp_path, "L (a@b.org)", get=lambda u, a: b"PK-new", pause=print)
    hashes = fetch.download(
        tmp_path, "L (a@b.org)", get=lambda u, a: b"PK-new", pause=print, allow_new_release=True
    )
    assert hashes["a.xlsx"] == hashlib.sha256(b"PK-new").hexdigest()


def test_download_checks_the_user_agent_first(tmp_path: Path) -> None:
    with pytest.raises(BlsFetchError):
        fetch.download(tmp_path, None, get=lambda u, a: pytest.fail("no request"), pause=print)


# -- parsers -------------------------------------------------------------------


def test_structure_finds_its_header_and_levels() -> None:
    rows = [
        *TITLE_BLOCK,
        ("Major Group", "Minor Group", "Broad Group", "Detailed Occupation", None),
        ("15-0000", None, None, None, "Computer and Mathematical Occupations"),
        (None, "15-1200", None, None, "Computer Occupations"),
        (None, None, "15-1250", None, "Software and Web Developers"),
        (None, None, None, "15-1252", "Software Developers"),
        (None, None, None, None, None),
    ]
    assert parse_structure(rows) == [
        {"code": "15-0000", "title": "Computer and Mathematical Occupations", "level": "major"},
        {"code": "15-1200", "title": "Computer Occupations", "level": "minor"},
        {"code": "15-1250", "title": "Software and Web Developers", "level": "broad"},
        {"code": "15-1252", "title": "Software Developers", "level": "detailed"},
    ]


def test_structure_refuses_a_changed_layout() -> None:
    with pytest.raises(BlsFetchError, match="header"):
        parse_structure([("Something else",)])
    with pytest.raises(BlsFetchError, match="column"):
        parse_structure([("Major Group",), ("15-1252", None, None, None, "Software Developers")])


def test_definitions_and_titles() -> None:
    defs = [
        *TITLE_BLOCK,
        ("SOC Group", "SOC Code", "SOC Title", "SOC Definition"),
        ("Broad", "15-1250", "Software and Web Developers", ""),
        ("Detailed", "15-1252", "Software Developers", "Research, design, and develop software."),
    ]
    assert parse_definitions(defs) == {"15-1252": "Research, design, and develop software."}
    titles = [
        *TITLE_BLOCK,
        ("2018 SOC Code", "2018 SOC Title", "2018 SOC Direct Match Title", "Illustrative Example"),
        ("11-1011", "Chief Executives", "CEO", ""),
        ("11-1011", "Chief Executives", "Admiral", "x"),
        ("11-1011", "Chief Executives", "CEO", ""),
    ]
    assert parse_direct_match_titles(titles) == {"11-1011": ["CEO", "Admiral"]}


EP_HEADER = ("2025 National Employment Matrix title", "2025 National Employment Matrix code")


def test_projections_keep_line_items_and_read_missing_values() -> None:
    rows = [
        ("Table 1.2 ...",),
        EP_HEADER,
        ("Total, all occupations", "00-0000", "Summary", "170280.8"),
        (
            "  Software developers",
            "15-1252",
            "Line item",
            "1717.8",
            "1892.6",
            "1",
            "1.1",
            "174.7",
            "10.2",
            "1.5",
            "95.3",
            "135980",
            "Bachelor's degree",
            "None",
            "None",
            "OOH Content",
            '<a href="https://www.bls.gov/ooh/software-developers.htm">OOH Content</a>',
        ),
        ("  Legislators", "11-1031", "Line item", "—", "—", *["—"] * 12),
        ("Note:",),
    ]
    out = parse_projections(rows)
    assert set(out) == {"15-1252", "11-1031"}
    dev = out["15-1252"]
    assert dev["employment_2025"] == 1_717_800  # published in thousands
    assert dev["employment_change_pct"] == 10.2
    assert dev["median_wage_annual"] == 135980.0
    assert dev["entry_education"] == "Bachelor's degree"
    assert dev["work_experience"] == "None"  # BLS's own category, kept as written
    assert dev["ooh_url"] == "https://www.bls.gov/ooh/software-developers.htm"
    legislators = out["11-1031"]
    assert legislators["median_wage_annual"] is None
    assert legislators["entry_education"] is None
    assert legislators["ooh_url"] is None


def test_skill_scores_take_one_score_per_skill_and_collect_onet_codes() -> None:
    rows = [
        ("Detailed skills data",),
        EP_HEADER,
        ("Data scientists", "15-2051", 1, 2, 3, 4, "10", "Mathematics", "4.7", "15-2041.00"),
        ("Data scientists", "15-2051", 1, 2, 3, 4, "10", "Mathematics", "4.7", "15-2041.00"),
        (
            "Data scientists",
            "15-2051",
            1,
            2,
            3,
            4,
            "2",
            "Computers and information technology",
            "4.2",
            "15-2051.01",
        ),
        ("Bad row", "x", 1, 2, 3, 4, "2", "Mathematics", "—", ""),
    ]
    scores, onet = parse_skill_scores(rows)
    assert scores == {"15-2051": {"Mathematics": 4.7, "Computers and information technology": 4.2}}
    assert onet == {"15-2051": ["15-2041.00", "15-2051.01"]}


def test_skill_crosswalk_groups_elements_under_their_skill() -> None:
    rows = [
        ("Crosswalk of O*NET elements",),
        ("EP skills ID", "EP skills title", "EP skills definition", "O*NET element ID"),
        ("2", "Computers and information technology", "Uses computers.", "2.B.3.e"),
        ("1", "Adaptability", "Adjusts behavior.", "1.C.4.a"),
        ("1", "Adaptability", "Adjusts behavior.", "1.C.4.b"),
    ]
    assert parse_skill_crosswalk(rows) == [
        {
            "ep_id": "1",
            "name": "Adaptability",
            "definition": "Adjusts behavior.",
            "onet_elements": ["1.C.4.a", "1.C.4.b"],
        },
        {
            "ep_id": "2",
            "name": "Computers and information technology",
            "definition": "Uses computers.",
            "onet_elements": ["2.B.3.e"],
        },
    ]


# -- assembling ----------------------------------------------------------------


def _structure(*detailed: str) -> list[dict[str, str]]:
    return [{"code": c, "title": f"Title {c}", "level": "detailed"} for c in detailed]


def _projection(code: str) -> dict[str, object]:
    return {"code": code, "title": "ep title", "median_wage_annual": 1.0}


def test_own_aggregate_and_combined_projections() -> None:
    doc = assemble(
        structure=[
            {"code": "31-1120", "title": "Home Health and Personal Care Aides", "level": "broad"},
            *_structure("15-1252", "31-1121", "21-1011", "21-1014", "11-1031"),
        ],
        definitions={},
        titles={"15-1252": ["Title 15-1252", "Programmer"]},
        projections={c: _projection(c) for c in ("15-1252", "31-1120", "21-1018")},
        scores={"31-1120": {"Interpersonal": 4.0}},
        onet_codes={"31-1120": ["31-1121.00", "31-1122.00"]},
        skills=[],
    )
    by_code = {o["code"]: o for o in doc["occupations"]}
    assert [g["code"] for g in doc["groups"]] == ["31-1120"]
    # Its own EP row.
    assert by_code["15-1252"]["projection"]["aggregate"] is False
    assert "title" not in by_code["15-1252"]["projection"]
    assert by_code["15-1252"]["alt_titles"] == ["Programmer"]  # the title itself is not an alias
    # Its broad group's EP row, flagged.
    aide = by_code["31-1121"]
    assert aide["projection"]["code"] == "31-1120"
    assert aide["projection"]["aggregate"] is True
    assert aide["skills"] == [{"name": "Interpersonal", "score": 4.0}]
    assert aide["onet_soc_codes"] == ["31-1121.00", "31-1122.00"]
    # A combined code that the digits cannot name.
    assert by_code["21-1011"]["projection"]["code"] == "21-1018"
    # No EP data at all.
    assert by_code["11-1031"]["projection"] is None
    assert by_code["11-1031"]["skills"] == []
    assert doc["meta"]["unmatched_projection_codes"] == []


def test_a_wrong_combined_code_member_stops_the_build() -> None:
    with pytest.raises(BlsFetchError, match="21-1018"):
        assemble(
            structure=_structure("21-1011"),  # 21-1014 is missing
            definitions={},
            titles={},
            projections={"21-1018": _projection("21-1018")},
            scores={},
            onet_codes={},
            skills=[],
        )
