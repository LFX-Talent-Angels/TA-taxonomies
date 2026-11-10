"""The glance-page parser keeps structure only (synthetic HTML, no network)."""

from ta_taxonomies.suites.sfia.fetch import incomplete_skills, parse_glance

_CELLS = "".join(
    f'<td class="level{" active" if n in (2, 3) else ""}">{n}</td>' for n in range(1, 8)
)
_HTML = f"""
<table>
<tr class="cat-1">
  <td><a href="/en/sfia-9/cats/development-and-implementation">Development and implementation</a>
  </td>
  <td><a href="/en/sfia-9/subcategories/systems-development">Systems development</a></td>
  <td><a href="/en/sfia-9/skills/programming-software-development">Programming</a>
  <span class="code">PROG</span>
  <p class="description">Licensed prose that must never be kept.</p></td>
  {_CELLS}
</tr>
<tr class="cat-1">
  <td></td><td></td>
  <td><a href="/en/sfia-9/skills/functional-testing">Functional testing</a>
  <span class="code">TEST</span></td>
  {_CELLS}
</tr>
</table>
"""


def test_parse_glance_keeps_codes_names_and_levels() -> None:
    doc = parse_glance(_HTML)

    assert [s["code"] for s in doc["skills"]] == ["PROG", "TEST"]
    test = doc["skills"][1]
    # Continuation rows inherit their category and subcategory.
    assert test["subcategory_slug"] == "systems-development"
    assert test["category_slug"] == "development-and-implementation"
    assert test["levels"] == [2, 3]
    assert test["slug"] == "functional-testing"
    assert doc["categories"] == [
        {"name": "Development and implementation", "slug": "development-and-implementation"}
    ]
    assert incomplete_skills(doc) == []


def test_parse_glance_never_keeps_prose() -> None:
    assert "Licensed prose" not in repr(parse_glance(_HTML))
