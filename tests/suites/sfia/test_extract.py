"""Extraction keeps the facts and drops the prose.

The HTML in this file is **synthetic**: it reproduces the markup shape of SFIA's
published pages with placeholder text of our own. That is deliberate — a test
fixture built from SFIA's actual prose would be the very thing ADR-0006 §2 keeps
out of this repository, and the parser cares about the markup, not the words.
"""

from __future__ import annotations

import pytest

from ta_taxonomies.suites.sfia.extract import (
    SfiaExtractError,
    extract_directory,
    extract_skill_page,
)

DIRECTORY_PAGE = """
<table>
  <tr><th>Title</th><th>Skill code</th><th>Description</th></tr>
  <tr>
    <td>
      <a href="https://example.invalid/en/sfia-9/skills/widget-wrangling">Widget wrangling</a>
    </td>
    <td>WIDG</td>
    <td>PLACEHOLDER PROSE that the extractor must not carry into the graph.</td>
    <td></td>
  </tr>
  <tr>
    <td>
      <a href="https://example.invalid/en/sfia-9/skills/all-skills-a-z">All skills A-Z</a>
    </td>
    <td>ZZZZ</td>
    <td>the page's link to itself</td>
    <td></td>
  </tr>
</table>
"""

# Level 1 renders as an empty section (this skill is not defined there); levels
# 2 and 3 carry text. Note that the section id sits *inside* a tag, which is the
# boundary the parser has to respect.
SKILL_PAGE = """
<div class="skill_level " id="skill_level_section_1">
  <h3><span>Level 1</span></h3>
  <div class="collapse" id="level-text-1">ESSENCE PLACEHOLDER for level 1.</div>
  <div class="level_text"><p></p></div>
</div>
<div class="skill_level " id="skill_level_section_2">
  <h3><span>Level 2</span></h3>
  <div class="collapse" id="level-text-2">ESSENCE PLACEHOLDER for level 2.</div>
  <div class="level_text"><p></p><div><p>DEFINITION PLACEHOLDER at level 2.</p></div></div>
</div>
<div class="skill_level " id="skill_level_section_3">
  <h3><span>Level 3</span></h3>
  <div class="collapse" id="level-text-3">ESSENCE PLACEHOLDER for level 3.</div>
  <div class="level_text"><p></p><div><p>DEFINITION PLACEHOLDER at level 3.</p></div></div>
</div>
<div id="viewlet-below-content-body"></div>
<aside id="related-sfia-skills">
  <h3>Related SFIA skills</h3>
  <div><span class="related-skill-link">
    <a href="https://example.invalid/en/sfia-9/skills/gadget-grooming">Gadget grooming</a>
  </span></div>
  <div><span class="related-skill-link">
    <a href="https://example.invalid/en/sfia-9/skills/gadget-grooming">Gadget grooming again</a>
  </span></div>
</aside>
"""


def test_the_directory_yields_code_name_and_slug_and_nothing_else() -> None:
    rows = extract_directory(DIRECTORY_PAGE)
    assert rows == [{"code": "WIDG", "name": "Widget wrangling", "slug": "widget-wrangling"}]
    # The description column exists in the input and must not reach the output.
    assert "PLACEHOLDER PROSE" not in repr(rows)
    assert set(rows[0]) == {"code", "name", "slug"}


def test_a_directory_whose_columns_moved_stops_the_load() -> None:
    """A short, plausible-looking load is worse than a failure.

    If the published table gains or reorders a column, the code cell stops being
    a code. Skipping such rows silently would produce a graph missing skills
    with every count internally consistent.
    """
    broken = DIRECTORY_PAGE.replace("<td>WIDG</td>", "<td>Not a code</td>")
    with pytest.raises(SfiaExtractError):
        extract_directory(broken)


def test_only_the_levels_with_content_are_reported() -> None:
    result = extract_skill_page(SKILL_PAGE)
    assert result["levels"] == [2, 3]


def test_an_empty_level_section_is_not_read_as_defined() -> None:
    """Regression: the section boundary is an attribute inside a tag.

    Slicing at the attribute leaves half an opening tag in the fragment, and a
    half tag has no ``>`` for the tag-stripper to match — so the markup survives
    as visible text and the emptiness test reads it as content. An earlier
    revision of this parser therefore reported all seven levels defined for all
    147 skills, which looked plausible and was wrong.
    """
    result = extract_skill_page(SKILL_PAGE)
    assert 1 not in result["levels"]
    assert len(result["levels"]) < 7


def test_no_licensed_text_survives_extraction() -> None:
    result = extract_skill_page(SKILL_PAGE)
    blob = repr(result)
    assert "DEFINITION PLACEHOLDER" not in blob
    assert "ESSENCE PLACEHOLDER" not in blob
    # What is left is integers and slugs.
    assert all(isinstance(level, int) for level in result["levels"])


def test_related_links_are_deduplicated_and_kept_in_page_order() -> None:
    result = extract_skill_page(SKILL_PAGE)
    assert result["related_slugs"] == ["gadget-grooming"]


def test_a_page_without_level_sections_is_an_error_not_an_empty_skill() -> None:
    with pytest.raises(SfiaExtractError):
        extract_skill_page("<html><body>nothing here</body></html>")
