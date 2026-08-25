"""The licence guard, tested as a guard rather than described as an intention.

TA-workspace ADR-0006 §2: SFIA's free licence covers personal and internal use;
redistributing SFIA material to another organisation needs a fee-bearing licence,
and a public Apache-2.0 repository is redistribution to everyone. This suite may
store skill codes, skill names, level numbers and structure — nothing else.

Every assertion here exists because a prose claim that no test covers is not
documentation, it is an assertion with no ``assert``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_taxonomies.suites.sfia.config import (
    ALLOWED_NODE_KEYS,
    LICENSED_FIELD_MARKERS,
    MAX_LABEL_CHARS,
)
from ta_taxonomies.suites.sfia.extract import SfiaLicenseError, assert_factual_only
from ta_taxonomies.suites.sfia.load import FIXTURE_PATH, load_fixture_document, normalize_document

GOOD_ROW = {
    "id": "sfia:skill:PROG",
    "source": "sfia",
    "source_id": "PROG",
    "pref_label": "Programming/software development",
    "code": "PROG",
    "kind": "SfiaSkill",
    "version": "9",
    "extra": {"levels": [2, 3, 4, 5, 6], "min_level": 2},
}


def test_a_factual_row_passes() -> None:
    assert_factual_only([GOOD_ROW], what="test")


def test_a_key_outside_the_allowlist_is_refused() -> None:
    """Adding a field has to be a deliberate edit to config.py, not a default."""
    with pytest.raises(SfiaLicenseError, match="ALLOWED_NODE_KEYS"):
        assert_factual_only([{**GOOD_ROW, "description": "anything"}], what="test")


@pytest.mark.parametrize(
    "key",
    ["description", "level_description", "essence_of_the_level", "guidance_notes", "levelText"],
)
def test_a_licensed_field_name_is_refused_even_nested_in_extra(key: str) -> None:
    """The allowlist only checks top-level keys; ``extra`` is a dict.

    Without this second mechanism, a description could ride into the graph
    inside ``extra`` under any name, because ``extra`` itself is allowed.
    """
    row = {**GOOD_ROW, "extra": {**GOOD_ROW["extra"], key: "x"}}
    with pytest.raises(SfiaLicenseError, match="licensed SFIA prose"):
        assert_factual_only([row], what="test")


def test_prose_smuggled_into_an_allowed_field_is_caught_by_length() -> None:
    """The first two checks ask *where* text was put; this one asks *what* it is.

    ``pref_label`` is an allowed key with an innocent name, so only the ceiling
    stands between it and a paragraph of SFIA's definition.
    """
    row = {**GOOD_ROW, "pref_label": "x" * (MAX_LABEL_CHARS + 1)}
    with pytest.raises(SfiaLicenseError, match="ceiling"):
        assert_factual_only([row], what="test")


def test_the_ceiling_is_read_from_config_rather_than_hardcoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test comparing against the constant cannot tell derived from agreeing.

    Lower the ceiling in config and the guard must follow; a literal that
    happens to equal today's value would leave this green.
    """
    monkeypatch.setattr("ta_taxonomies.suites.sfia.extract.MAX_LABEL_CHARS", 5)
    with pytest.raises(SfiaLicenseError):
        assert_factual_only([{**GOOD_ROW, "pref_label": "far too long"}], what="test")


def test_the_committed_fixture_carries_no_prose() -> None:
    """The fixture is published; it gets checked as published, not as intended."""
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    document = json.loads(raw)

    for skill in document["skills"]:
        assert set(skill) == {"code", "name", "slug", "levels", "related_slugs"}
        assert len(skill["name"]) <= MAX_LABEL_CHARS
        assert all(isinstance(level, int) for level in skill["levels"])

    # No string value anywhere in the file is long enough to be a sentence of
    # SFIA's text. Walking the parsed values rather than the raw lines matters:
    # a line-based check over JSON skips almost every line and proves nothing.
    # `meta.note` is this suite's own sentence about the licence, not SFIA's.
    long_values = [
        (path, value)
        for path, value in _strings(document)
        if len(value) > MAX_LABEL_CHARS and path != "meta.note"
    ]
    assert not long_values, long_values


def test_every_normalized_node_row_is_within_the_allowlist() -> None:
    payload = normalize_document(load_fixture_document())
    for key in ("levels", "categories", "subcategories", "skills"):
        for row in payload[key]:
            assert set(row) <= set(ALLOWED_NODE_KEYS)
        assert_factual_only(payload[key], what=key)


def test_the_suite_source_tree_never_mentions_a_description_property() -> None:
    """A grep, deliberately: the guards run at load time, this runs at review time.

    ESCO's and O*NET's loaders both carry a ``description`` node property, and
    this suite is a near-copy of theirs. The likeliest way SFIA prose reaches
    the graph is somebody restoring that line while porting a change across.
    """
    package = Path(__file__).resolve().parents[3] / "src" / "ta_taxonomies" / "suites" / "sfia"
    offenders = []
    for path in sorted(package.glob("*.py")):
        if path.name == "config.py":
            # config.py names these words on purpose — it holds the denylist.
            # It is covered instead by the allowlist assertion below.
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if "ArgumentParser(" in code:
                # argparse's own `description=` is the CLI's help text, not a
                # node property. Narrowing here rather than loosening the match
                # keeps the check pointed at the thing it is for.
                continue
            if "n.description" in code or '"description"' in code or "description=" in code:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"description property assigned at {offenders}"


def test_no_allowlisted_key_names_licensed_prose() -> None:
    """The two config lists must not contradict each other.

    ``ALLOWED_NODE_KEYS`` says what a node may carry and ``LICENSED_FIELD_MARKERS``
    says what it may never carry. Adding ``description`` to the first would make
    the allowlist wave through exactly what the second exists to stop, and the
    two lists live twenty lines apart in the same file.
    """
    for key in ALLOWED_NODE_KEYS:
        assert not any(marker in key.lower() for marker in LICENSED_FIELD_MARKERS), key


def _strings(value: object, path: str = "") -> list[tuple[str, str]]:
    """Every string in a parsed JSON document, with its dotted path."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [item for k, v in value.items() for item in _strings(v, f"{path}.{k}".lstrip("."))]
    if isinstance(value, list):
        return [
            item for i, v in enumerate(value) for item in _strings(v, f"{path}.{i}".lstrip("."))
        ]
    return []


def test_the_loader_itself_refuses_a_document_carrying_prose(tmp_path: Path) -> None:
    """The guard is *wired in*, not merely available.

    Every other test in this file calls ``assert_factual_only`` directly, so
    deleting the call from ``run_load`` would leave them all green. This one goes
    through the loader's own path — and raises before any Neo4j connection is
    opened, which is why it needs no database.
    """
    from ta_taxonomies.suites.sfia.load import run_load

    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    document["skills"][0]["name"] = "A sentence of prose " * 10
    smuggled = tmp_path / "smuggled.json"
    smuggled.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SfiaLicenseError, match="ceiling"):
        run_load(mode="fixture", fixture_path=smuggled)
