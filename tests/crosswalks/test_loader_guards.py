"""Loader safety properties, checked without a database.

The crosswalk loader runs on top of two populated suites. The two ways it
could do real damage are wiping data it does not own and inventing endpoints
it cannot justify, so both are pinned here by reading the module's own source
and behaviour rather than trusting review to catch a regression.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from ta_taxonomies.crosswalks import load as load_module
from ta_taxonomies.crosswalks.config import (
    REL_ASSERTED_CORRESPONDS_TO,
    REL_CORRESPONDS_TO,
    TRAVERSABLE_RELS,
)


def _cypher_fragments() -> list[str]:
    """Every string literal in load.py that looks like Cypher.

    Read from the AST rather than the raw file so that prose in docstrings --
    which necessarily talks about DELETE and about suite labels in order to
    explain the rule -- cannot trip the checks below.
    """
    tree = ast.parse(inspect.getsource(load_module))

    # Docstrings explain the rules being checked, so they necessarily mention
    # DELETE and suite labels. Exclude them by identity before scanning.
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }

    # ast.walk also yields an f-string's literal pieces on their own. Scanning
    # those would see half a query and report a DELETE with its label sliced
    # off, so only the assembled JoinedStr is kept.
    inside_fstring = {
        id(piece)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for piece in ast.walk(node)
        if piece is not node
    }

    fragments: list[str] = []
    for node in ast.walk(tree):
        if id(node) in docstrings or id(node) in inside_fstring:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            fragments.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            # f-strings: keep the literal parts and mark interpolations, so a
            # label injected via a config constant is still visible as text.
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    parts.append(ast.unparse(value.value))
            fragments.append("".join(parts))
    keywords = ("MATCH", "MERGE", "DELETE", "CREATE", "SET ")
    return [f for f in fragments if any(k in f for k in keywords)]


class TestNeverDeletesSuiteData:
    def test_no_delete_targets_a_suite_label(self) -> None:
        """No Cypher here may DETACH DELETE anything a suite owns."""
        suite_labels = ("Occupation", "Skill", "ISCOGroup", "SkillGroup", "EscoNode")
        offenders = [
            (fragment, label)
            for fragment in _cypher_fragments()
            if "DELETE" in fragment
            for label in suite_labels
            if f":{label}" in fragment
        ]
        assert not offenders, f"delete statements touch suite labels: {offenders}"

    def test_deletes_are_confined_to_crosswalk_owned_types(self) -> None:
        """Whatever is deleted must be a type or label this layer created."""
        # Both the literal names and the config constants that render to them,
        # since interpolated labels appear in the AST as the constant's name.
        owned = (
            "CORRESPONDS_TO",
            "NoLink",
            "LABEL_NO_LINK",
            "CrosswalkSource",
            "LABEL_CROSSWALK_SOURCE",
            "RECORDED_NO_LINK",
        )
        for fragment in _cypher_fragments():
            if "DELETE" not in fragment:
                continue
            assert any(name in fragment for name in owned), (
                f"delete statement targets nothing crosswalk-owned: {fragment!r}"
            )

    def test_reset_is_scoped_by_source_key(self) -> None:
        """Reloading one crosswalk must leave every other crosswalk intact."""
        source = inspect.getsource(load_module.reset_source)
        assert source.count("$key") >= 2
        assert "source_key = $key" in source
        assert "checked_against = $key" in source

    def test_no_unscoped_wipe_helper_exists(self) -> None:
        assert not any(name.startswith("wipe") for name in dir(load_module)), (
            "the crosswalk layer must not offer a wipe helper"
        )


class TestNeverInventsEndpoints:
    def test_merge_matches_endpoints_and_never_creates_them(self) -> None:
        """Endpoints are MATCHed. A MERGE on an endpoint would conjure occupations."""
        cypher = next(f for f in _cypher_fragments() if "REL_CORRESPONDS_TO" in f and "MERGE" in f)
        assert "MATCH (a {id: row.from_id})" in cypher
        assert "MATCH (b {id: row.to_id})" in cypher
        assert "MERGE (a " not in cypher and "MERGE (b " not in cypher
        assert "MERGE (a:" not in cypher and "MERGE (b:" not in cypher

    def test_endpoint_seeding_is_unreachable_from_full_mode(self) -> None:
        """Fixture scaffolding must not be callable on a real load."""
        source = inspect.getsource(load_module.load_crosswalk)
        seeding = next(line for line in source.splitlines() if "seed_fixture_endpoints" in line)
        assert 'if mode == "fixture"' in source
        assert source.index('if mode == "fixture"') < source.index(seeding)


class TestModeIsRequired:
    def test_mode_has_no_default(self) -> None:
        """--mode must be given explicitly; a default is how a graph gets clobbered."""
        with pytest.raises(SystemExit):
            load_module.main([])

    def test_unknown_mode_is_refused(self) -> None:
        with pytest.raises(SystemExit):
            load_module.main(["--mode", "everything"])


class TestClaimsAreNotPublishedData:
    def test_published_and_asserted_are_different_relationship_types(self) -> None:
        assert REL_CORRESPONDS_TO != REL_ASSERTED_CORRESPONDS_TO

    def test_claims_are_not_traversable_by_default(self) -> None:
        """A query written for published data cannot reach project opinion."""
        assert REL_ASSERTED_CORRESPONDS_TO not in TRAVERSABLE_RELS
        assert REL_CORRESPONDS_TO in TRAVERSABLE_RELS
