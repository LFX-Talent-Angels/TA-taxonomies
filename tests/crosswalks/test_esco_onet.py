"""Normalization of the published ESCO/O*NET table, and the traps in it.

The trailing-zero tests are the important ones. They are not hypothetical:
ESCO 0110.10 (lieutenant) is in the published table and 0110.1 (air force
officer) is not, so a numeric round-trip of the key column would hand 'air
force officer' three American police occupations and look entirely plausible
doing it.
"""

from __future__ import annotations

import pytest

from ta_taxonomies.crosswalks.esco_onet import (
    CrosswalkFormatError,
    classify_esco_code,
    onet_occupation_id,
    parse_rows,
    require_code_str,
    resolve,
)
from ta_taxonomies.crosswalks.load import load_fixture_document
from ta_taxonomies.crosswalks.models import MatchStrength
from ta_taxonomies.crosswalks.sources import ESCO_ONET_2019


class TestCodesStayStrings:
    @pytest.mark.parametrize("value", [110.1, 2512, 0.0])
    def test_numeric_codes_are_refused_not_coerced(self, value: float | int) -> None:
        """By the time a code is a float the zeros are gone; there is nothing to salvage."""
        with pytest.raises(CrosswalkFormatError, match="codes must stay strings"):
            require_code_str(value, field_name="esco_code")

    def test_leading_and_trailing_zeros_survive(self) -> None:
        assert require_code_str("0110.10", field_name="c") == "0110.10"
        assert require_code_str("0110.1", field_name="c") == "0110.1"

    def test_the_two_are_not_the_same_code(self) -> None:
        assert require_code_str("0110.10", field_name="c") != require_code_str(
            "0110.1", field_name="c"
        )


class TestCodeClassification:
    @pytest.mark.parametrize("code", ["2512.4", "0110.10", "2131.4.12"])
    def test_dotted_codes_are_occupations(self, code: str) -> None:
        assert classify_esco_code(code) == "occupation"

    @pytest.mark.parametrize("code", ["2512", "0110", "0", "011"])
    def test_bare_digits_are_isco_groups(self, code: str) -> None:
        assert classify_esco_code(code) == "isco_group"

    def test_garbage_is_refused(self) -> None:
        with pytest.raises(CrosswalkFormatError):
            classify_esco_code("15-1252.00")


class TestOnetIds:
    def test_agreed_id_shape(self) -> None:
        """Shape agreed with the O*NET suite owner: <suite>:<type>:<local>."""
        assert onet_occupation_id("15-1252.00") == "onet:occupation:15-1252.00"

    @pytest.mark.parametrize("code", ["15-1252.0", "15-1252", "151252.00", "15-1252.000", ""])
    def test_malformed_codes_are_refused(self, code: str) -> None:
        with pytest.raises(CrosswalkFormatError):
            onet_occupation_id(code)

    def test_detail_suffix_is_preserved(self) -> None:
        """'.00' and '.07' are different occupations inside the same SOC group."""
        assert onet_occupation_id("15-1299.07").endswith("15-1299.07")


class TestParseRows:
    def test_bad_row_names_itself(self) -> None:
        with pytest.raises(CrosswalkFormatError, match="row 1"):
            parse_rows(
                [
                    {"esco_code": "2512.4", "onet_code": "15-1252.00"},
                    {"esco_code": "2512.4", "onet_code": "nonsense"},
                ]
            )


class TestResolveAgainstFixture:
    @pytest.fixture
    def document(self) -> dict:
        return load_fixture_document()

    @pytest.fixture
    def resolution(self, document: dict):
        return resolve(
            parse_rows(document["rows"]),
            occupation_ids_by_code=document["esco_occupation_ids_by_code"],
            isco_ids_by_code=document["esco_isco_ids_by_code"],
            provenance=ESCO_ONET_2019,
            all_esco_occupation_codes=document["all_esco_occupation_codes"],
        )

    def test_counts(self, resolution) -> None:
        # 8 resolvable rows; the 9th names an ESCO code this release does not have.
        assert resolution.stats["correspondences"] == 8
        assert resolution.unresolved_esco_codes == ["9999.9"]

    def test_trailing_zero_pair_does_not_bleed(self, resolution, document: dict) -> None:
        """0110.10 gets its links; 0110.1 gets an explicit absence, not 0110.10's rows."""
        lieutenant = document["esco_occupation_ids_by_code"]["0110.10"]
        air_force = document["esco_occupation_ids_by_code"]["0110.1"]

        linked = {c.from_id for c in resolution.correspondences}
        assert lieutenant in linked
        assert air_force not in linked

        absences = {n.from_id for n in resolution.no_links}
        assert air_force in absences
        assert lieutenant not in absences

    def test_isco_group_rows_land_on_the_group_node(self, resolution) -> None:
        """Group-level rows must not be attached to occupations inside the group."""
        from_group = [c for c in resolution.correspondences if c.from_id == "esco:isco:2512"]
        assert len(from_group) == 2
        assert all(c.to_id.startswith("onet:occupation:") for c in from_group)

    def test_many_to_many_is_preserved(self, resolution, document: dict) -> None:
        developer = document["esco_occupation_ids_by_code"]["2512.4"]
        targets = {c.to_id for c in resolution.correspondences if c.from_id == developer}
        assert len(targets) == 3, "collapsing a many-to-many row to one target loses data"

    def test_strength_is_unspecified_because_the_source_omits_it(self, resolution) -> None:
        assert all(c.strength is MatchStrength.UNSPECIFIED for c in resolution.correspondences)

    def test_absences_cite_the_table_they_were_checked_against(self, resolution) -> None:
        assert all(n.checked_against == ESCO_ONET_2019.key for n in resolution.no_links)

    def test_source_row_carries_codes_not_prose(self, resolution) -> None:
        """Pointer, not payload: publisher titles stay out of the graph."""
        for correspondence in resolution.correspondences:
            assert set(correspondence.source_row) == {"esco_code", "onet_code"}


class TestProvenanceRegistry:
    def test_the_published_crosswalk_is_marked_model_assisted(self) -> None:
        """Published is about who; method is about how. Do not conflate them."""
        assert ESCO_ONET_2019.method.value == "model_assisted_validated"

    def test_caveats_are_recorded(self) -> None:
        assert any("strength column" in c for c in ESCO_ONET_2019.caveats)
