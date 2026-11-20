"""Schema statements are suite-local and do not rewrite ESCO indexes."""

from ta_taxonomies.suites.onet.config import FULLTEXT_INDEX, LABEL_ONET_NODE
from ta_taxonomies.suites.onet.schema import CONSTRAINTS, FULLTEXT_INDEXES, INDEXES


def test_onet_node_id_is_unique() -> None:
    assert any(f"FOR (n:{LABEL_ONET_NODE}) REQUIRE n.id IS UNIQUE" in stmt for stmt in CONSTRAINTS)


def test_fulltext_index_is_on_onet_node_only() -> None:
    joined = "\n".join(FULLTEXT_INDEXES)
    assert FULLTEXT_INDEX in joined
    assert f"FOR (n:{LABEL_ONET_NODE})" in joined
    assert "Occupation|Skill" not in joined
    assert "standard-no-stop-words" in joined


def test_schema_does_not_recreate_occupation_unique() -> None:
    joined = "\n".join(CONSTRAINTS + INDEXES)
    assert "FOR (n:Occupation)" not in joined
    assert "FOR (n:Skill)" not in joined
