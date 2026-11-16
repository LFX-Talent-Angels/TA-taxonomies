"""Fetch the pinned JobTech files and build the one JSON document ``load.py`` reads.

Use case::

    python -m ta_taxonomies.suites.jobtech.fetch               # download + build
    python -m ta_taxonomies.suites.jobtech.fetch --no-download # build from data/jobtech/raw

Sources (version 31, no key needed):

* two bulk files from data.jobtechdev.se, each checked against its SHA-256;
* the occupations' direct essential/optional skills, which no bulk file
  holds, from the public GraphQL API (paged, pinned to the same version).

``build_document`` keeps only what the graph uses. The parsing works on plain
dicts, so tests need no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ta_taxonomies.suites.jobtech.config import ESCO_MATCH_KINDS, TAXONOMY_VERSION
from ta_taxonomies.suites.jobtech.ids import CONCEPT_ID, SSYK_CODE

DEFAULT_RAW_DIR = Path("data/jobtech/raw")
DEFAULT_OUT = Path("data/jobtech/jobtech_document.json")
BULK_BASE = f"https://data.jobtechdev.se/taxonomy/version/{TAXONOMY_VERSION}/query"
GRAPHQL_URL = "https://taxonomy.api.jobtechdev.se/v1/taxonomy/graphql"
USER_AGENT = "TalentAngels-loader/0.1"
TIMEOUT_S = 180
PAUSE_S = 1.0
PAGE = 400
DIRECT_SKILLS_FILE = "occupation-essential-optional-skills.json"


@dataclass(frozen=True)
class BulkFile:
    name: str
    sha256: str

    @property
    def filename(self) -> str:
        return f"{self.name}.json"

    @property
    def url(self) -> str:
        return f"{BULK_BASE}/{self.name}/{self.name}.json"


BULK_FILES: tuple[BulkFile, ...] = (
    BulkFile(
        "concepts-and-common-relations",
        "634fd9d848a172747e54e3e487160a912ae6c0fcb8219c50998767c8c9aacbcc",
    ),
    BulkFile(
        "substitutability-relations-between-occupations",
        "8761f938505ab9f276b8054d235c9a574bf2da1ff56c3761d9ce836f6fc93c60",
    ),
)

_ISCO_URI = re.compile(r"/isco/C([0-9]{4})$")
_MATCH_FIELDS = {kind: f"{kind}_match" for kind in ESCO_MATCH_KINDS}


class JobtechFetchError(RuntimeError):
    """A download or a source file is not what this loader was built for."""


# -- download ------------------------------------------------------------------


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        data: bytes = response.read()
        return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_bulk(
    raw_dir: Path,
    *,
    allow_new_release: bool = False,
    get: Callable[[str], bytes] = _http_get,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    """Download each pinned bulk file that is missing or stale; return name → SHA-256."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    first = True
    for bulk in BULK_FILES:
        path = raw_dir / bulk.filename
        if path.exists() and _sha256(path.read_bytes()) == bulk.sha256:
            hashes[bulk.name] = bulk.sha256
            continue
        if not first:
            pause(PAUSE_S)
        first = False
        data = get(bulk.url)
        if not data.lstrip().startswith(b"{"):
            raise JobtechFetchError(f"{bulk.url} did not return JSON")
        digest = _sha256(data)
        if digest != bulk.sha256 and not allow_new_release:
            raise JobtechFetchError(
                f"{bulk.filename} changed (sha256 {digest}); re-check it, then re-run with "
                "--allow-new-release and update the pins."
            )
        path.write_bytes(data)
        hashes[bulk.name] = digest
    return hashes


_DIRECT_QUERY = (
    'query {{ concepts(type: "occupation-name", limit: {limit}, offset: {offset}, '
    'version: "{version}") {{ id essential {{ id type }} optional {{ id type }} }} }}'
)


def download_direct_skills(
    raw_dir: Path,
    *,
    get: Callable[[str], bytes] = _http_get,
    pause: Callable[[float], None] = time.sleep,
) -> Path:
    """Page the occupations' direct essential/optional skills out of GraphQL."""
    concepts: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = _DIRECT_QUERY.format(limit=PAGE, offset=offset, version=TAXONOMY_VERSION)
        payload = json.loads(get(f"{GRAPHQL_URL}?{urllib.parse.urlencode({'query': query})}"))
        if payload.get("errors"):
            raise JobtechFetchError(f"GraphQL error: {payload['errors']}")
        page = payload["data"]["concepts"]
        concepts.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
        pause(PAUSE_S)
    concepts.sort(key=lambda c: c["id"])
    path = raw_dir / DIRECT_SKILLS_FILE
    raw_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": TAXONOMY_VERSION, "data": {"concepts": concepts}}, indent=0),
        encoding="utf-8",
    )
    return path


# -- building ------------------------------------------------------------------


def _concepts(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        concepts = doc["data"]["concepts"]
    except (KeyError, TypeError) as exc:
        raise JobtechFetchError("not a JobTech query file (no data.concepts)") from exc
    return list(concepts)


def _ids(items: Iterable[Mapping[str, Any]] | None) -> list[str]:
    return [str(item["id"]) for item in items or [] if item.get("id")]


def _check_id(cid: str) -> str:
    if not CONCEPT_ID.fullmatch(cid):
        raise JobtechFetchError(f"unexpected concept id {cid!r}")
    return cid


def _labels(concept: Mapping[str, Any]) -> list[str]:
    label = concept.get("preferred_label") or ""
    seen: list[str] = []
    for alt in [*(concept.get("alternative_labels") or []), *(concept.get("hidden_labels") or [])]:
        if alt and alt != label and alt not in seen:
            seen.append(str(alt))
    return seen


def build_document(
    common: Mapping[str, Any],
    substitutability: Mapping[str, Any],
    direct: Mapping[str, Any] | None,
    *,
    files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One document: SSYK groups, occupations, skills, headlines, substitutions."""
    concepts = _concepts(common)
    by_id = {c["id"]: c for c in concepts}

    def of_type(cid: str, *types: str) -> bool:
        return by_id.get(cid, {}).get("type") in types

    def esco(concept: Mapping[str, Any], target_type: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for kind, field in _MATCH_FIELDS.items():
            uris = [
                str(by_id[cid]["esco_uri"])
                for cid in _ids(concept.get(field))
                if of_type(cid, target_type) and by_id[cid].get("esco_uri")
            ]
            out[kind] = sorted(set(uris))
        return out

    ssyk_code_by_id: dict[str, str] = {}
    groups: list[dict[str, Any]] = []
    for c in concepts:
        if not str(c.get("type", "")).startswith("ssyk-level-"):
            continue
        code = str(c.get("ssyk_code_2012") or "")
        level = int(c["type"].rsplit("-", 1)[1])
        if not SSYK_CODE.fullmatch(code) or len(code) != level:
            raise JobtechFetchError(f"SSYK concept {c['id']} has code {code!r} at level {level}")
        ssyk_code_by_id[c["id"]] = code
    for c in concepts:
        cid = c["id"]
        if cid not in ssyk_code_by_id:
            continue
        code = ssyk_code_by_id[cid]
        parents = [ssyk_code_by_id[p] for p in _ids(c.get("broader")) if p in ssyk_code_by_id]
        if len(code) > 1 and parents != [code[:-1]]:
            raise JobtechFetchError(f"SSYK {code}: parent {parents}, expected {code[:-1]}")
        groups.append(
            {
                "concept_id": _check_id(cid),
                "code": code,
                "level": len(code),
                "label": c.get("preferred_label") or "",
                "definition": c.get("definition") or "",
                "parent": parents[0] if parents else None,
            }
        )

    direct_by_occ: dict[str, dict[str, list[str]]] = {}
    for c in _concepts(direct) if direct else []:
        direct_by_occ[c["id"]] = {
            "essential": sorted(i for i in _ids(c.get("essential")) if of_type(i, "skill")),
            "optional": sorted(i for i in _ids(c.get("optional")) if of_type(i, "skill")),
        }

    occupations: list[dict[str, Any]] = []
    for c in concepts:
        if c.get("type") != "occupation-name":
            continue
        cid = _check_id(c["id"])
        unit = [ssyk_code_by_id[p] for p in _ids(c.get("broader")) if p in ssyk_code_by_id]
        if len(unit) != 1 or len(unit[0]) != 4:
            raise JobtechFetchError(f"occupation {cid} has SSYK unit groups {unit}")
        isco = sorted(
            {
                m.group(1)
                for p in _ids(c.get("broader"))
                if of_type(p, "isco-level-4")
                and (m := _ISCO_URI.search(str(by_id[p].get("esco_uri") or "")))
            }
        )
        skills = direct_by_occ.get(cid, {"essential": [], "optional": []})
        occupations.append(
            {
                "concept_id": cid,
                "label": c.get("preferred_label") or "",
                "alt_labels": _labels(c),
                "definition": c.get("definition") or "",
                "ssyk_code": unit[0],
                "isco_codes": isco,
                "esco": esco(c, "esco-occupation"),
                "essential": skills["essential"],
                "optional": [s for s in skills["optional"] if s not in skills["essential"]],
            }
        )

    skills_out: list[dict[str, Any]] = []
    headline_ids: set[str] = set()
    for c in concepts:
        if c.get("type") != "skill":
            continue
        cid = _check_id(c["id"])
        heads = [p for p in _ids(c.get("broader")) if of_type(p, "skill-headline")]
        if len(heads) != 1:
            raise JobtechFetchError(f"skill {cid} has headlines {heads}")
        headline_ids.add(heads[0])
        skills_out.append(
            {
                "concept_id": cid,
                "label": c.get("preferred_label") or "",
                "alt_labels": _labels(c),
                "definition": c.get("definition") or "",
                "headline": heads[0],
                "ssyk_codes": sorted(
                    {
                        ssyk_code_by_id[p]
                        for p in _ids(c.get("related"))
                        if of_type(p, "ssyk-level-4") and p in ssyk_code_by_id
                    }
                ),
                "esco": esco(c, "esco-skill"),
            }
        )
    headlines = [
        {"concept_id": _check_id(h), "label": by_id[h].get("preferred_label") or ""}
        for h in sorted(headline_ids)
    ]

    occupation_ids = {o["concept_id"] for o in occupations}
    substitutions = sorted(
        (
            {"from": c["id"], "to": target["id"], "pct": int(target["substitutability_percentage"])}
            for c in _concepts(substitutability)
            for target in c.get("substitutes") or []
            if c["id"] in occupation_ids and target.get("id") in occupation_ids
        ),
        key=lambda s: (s["from"], s["to"]),
    )

    groups.sort(key=lambda g: (g["level"], g["code"]))
    occupations.sort(key=lambda o: o["concept_id"])
    skills_out.sort(key=lambda s: s["concept_id"])
    return {
        "meta": {
            "taxonomy_version": TAXONOMY_VERSION,
            "files": dict(files or {}),
            "direct_skills": direct is not None,
        },
        "ssyk_groups": groups,
        "occupations": occupations,
        "skills": skills_out,
        "headlines": headlines,
        "substitutions": substitutions,
    }


def build_from_dir(raw_dir: Path) -> dict[str, Any]:
    paths = {bulk.name: raw_dir / bulk.filename for bulk in BULK_FILES}
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise JobtechFetchError(f"missing: {', '.join(missing)} (run without --no-download)")
    direct_path = raw_dir / DIRECT_SKILLS_FILE
    direct = json.loads(direct_path.read_text(encoding="utf-8")) if direct_path.exists() else None
    if direct is not None and direct.get("version") not in (None, TAXONOMY_VERSION):
        raise JobtechFetchError(f"{direct_path} is for version {direct.get('version')}")
    files = {name: _sha256(path.read_bytes()) for name, path in paths.items()}
    return build_document(
        json.loads(paths["concepts-and-common-relations"].read_text(encoding="utf-8")),
        json.loads(
            paths["substitutability-relations-between-occupations"].read_text(encoding="utf-8")
        ),
        direct,
        files=files,
    )


def summary(doc: Mapping[str, Any]) -> dict[str, Any]:
    occ = doc["occupations"]
    return {
        "ssyk_groups": len(doc["ssyk_groups"]),
        "occupations": len(occ),
        "skills": len(doc["skills"]),
        "headlines": len(doc["headlines"]),
        "substitutions": len(doc["substitutions"]),
        "occupations_with_direct_skills": sum(1 for o in occ if o["essential"] or o["optional"]),
        "occupations_with_exact_esco": sum(1 for o in occ if o["esco"]["exact"]),
        "direct_skills_loaded": doc["meta"]["direct_skills"],
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch JobTech files and build the document")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--allow-new-release", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if not args.no_download:
            download_bulk(args.raw_dir, allow_new_release=args.allow_new_release)
            download_direct_skills(args.raw_dir)
        doc = build_from_dir(args.raw_dir)
    except (JobtechFetchError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out), **summary(doc)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
