"""BLS titles are plural; the singular a user types is kept as an alias."""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.bls.load import singular_title


@pytest.mark.parametrize(
    ("title", "singular"),
    [
        ("Registered Nurses", "Registered Nurse"),
        ("Software Developers", "Software Developer"),
        ("Bakers", "Baker"),
        ("Secretaries", "Secretary"),
        ("Nurse Midwives", "Nurse Midwife"),
        ("Coaches", "Coach"),
        ("Dentists, General", "Dentist"),
        (
            "First-Line Supervisors of Security Workers",
            "First-Line Supervisor of Security Workers",
        ),
    ],
)
def test_plain_plural_titles_get_their_singular(title: str, singular: str) -> None:
    assert singular_title(title) == singular


@pytest.mark.parametrize(
    "title",
    [
        "Chefs and Head Cooks",
        "Lawyers, Judges, and Related Workers",
        "Helpers--Electricians",
        "Clergy",
    ],
)
def test_titles_naming_several_jobs_get_none(title: str) -> None:
    assert singular_title(title) is None
