"""The embed batch jobs must reach every node, not every other batch.

Regression: the fetch query filtered on ``label_embedding IS NULL`` and paged
with an advancing ``SKIP``. Each written batch dropped out of the filter, so the
offset jumped over an unembedded batch every round and a full load ended with
~50% of nodes embedded (ESCO 9,216 / 18,237; O*NET 16,070 / 31,942).
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from ta_taxonomies.suites.esco import embed as esco_embed
from ta_taxonomies.suites.onet import embed as onet_embed


class _Vec(list[float]):
    def tolist(self) -> list[float]:
        return list(self)


class _FakeModel:
    def __init__(self, _name: str) -> None:
        pass

    def encode(self, labels: list[str], normalize_embeddings: bool = True) -> list[_Vec]:
        return [_Vec([0.0]) for _ in labels]


class _FakeSession:
    """Evaluates the fetch/write queries the way Neo4j would."""

    def __init__(self, store: dict[str, list[float] | None]) -> None:
        self.store = store

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def run(self, query: str, **params: Any) -> Any:
        if "SET n.label_embedding" in query:
            for row in params["rows"]:
                self.store[row["id"]] = row["embedding"]
            return types.SimpleNamespace(data=lambda: [])
        assert "SKIP" not in query, "offset paging is unsafe with the IS NULL filter"
        after = params.get("after")
        ids = sorted(
            i
            for i, emb in self.store.items()
            if (params["force"] or emb is None) and (after is None or i > after)
        )
        rows = [{"id": i, "pref_label": i} for i in ids[: params["batch"]]]
        return types.SimpleNamespace(data=lambda: rows)


class _FakeDriver:
    def __init__(self, store: dict[str, list[float] | None]) -> None:
        self.store = store

    def session(self) -> _FakeSession:
        return _FakeSession(self.store)


@pytest.fixture(autouse=True)
def _fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)


@pytest.mark.parametrize("module", [esco_embed, onet_embed])
@pytest.mark.parametrize("force", [False, True])
def test_every_node_is_embedded(module: Any, force: bool) -> None:
    n = module.BATCH_SIZE * 3 + 7
    store: dict[str, list[float] | None] = {f"id-{i:06d}": None for i in range(n)}

    updated = module.build_embeddings(_FakeDriver(store), force=force)

    assert updated == n
    assert all(v is not None for v in store.values())


@pytest.mark.parametrize("module", [esco_embed, onet_embed])
def test_rerun_fills_only_the_gaps(module: Any) -> None:
    store: dict[str, list[float] | None] = {
        f"id-{i:06d}": ([1.0] if i % 2 else None) for i in range(module.BATCH_SIZE * 2)
    }

    updated = module.build_embeddings(_FakeDriver(store), force=False)

    assert updated == module.BATCH_SIZE
    assert all(v is not None for v in store.values())


def test_query_model_is_loaded_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier-5 search built a new SentenceTransformer on every query; now one
    model serves every suite (it is ~400 MB in memory)."""
    from ta_taxonomies.suites import _locate
    from ta_taxonomies.suites.esco import tools as esco_tools
    from ta_taxonomies.suites.onet import tools as onet_tools

    built: list[str] = []

    class Counting(_FakeModel):
        def __init__(self, name: str) -> None:
            built.append(name)

    monkeypatch.setattr(sys.modules["sentence_transformers"], "SentenceTransformer", Counting)
    _locate.embedding_model.cache_clear()
    for module in (esco_tools, onet_tools):
        module._embed_query("a")
        module._embed_query("b")
    _locate.embedding_model.cache_clear()
    assert len(built) == 1


@pytest.mark.parametrize("module_name", ["esco", "onet"])
def test_vector_search_has_a_relevance_floor(module_name: str) -> None:
    from ta_taxonomies.suites._locate import queries_for
    from ta_taxonomies.suites.esco.tools import ESCO_LOCATE
    from ta_taxonomies.suites.onet.tools import ONET_LOCATE

    config = {"esco": ESCO_LOCATE, "onet": ONET_LOCATE}[module_name]
    assert 0.7 < config.vector_min_score < 0.8
    assert "score >= $min_score" in queries_for(config).vector
