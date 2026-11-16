"""The meaning search fetches a wide pool before it filters by kind.

Each suite has one vector index over every kind, and the kind filter runs
after the nearest-neighbour call. With a pool the size of the answer, the
commonest kind crowds the others out: O*NET's tasks hid 27 of 42 matching
occupations from job-title queries. The pool must stay well above the answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from ta_taxonomies.suites._locate import LocateConfig, Locator
from ta_taxonomies.suites.bls.tools import BLS_LOCATE
from ta_taxonomies.suites.esco.tools import ESCO_LOCATE
from ta_taxonomies.suites.jobtech.tools import JOBTECH_LOCATE
from ta_taxonomies.suites.onet.tools import ONET_LOCATE
from ta_taxonomies.suites.sfia.tools import SFIA_LOCATE

CONFIGS = [ESCO_LOCATE, ONET_LOCATE, SFIA_LOCATE, BLS_LOCATE, JOBTECH_LOCATE]


class _Recorder:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def run(self, query: str, **params: Any) -> list[Any]:
        self.params = params
        return []


@pytest.mark.parametrize("config", CONFIGS, ids=lambda c: c.source)
def test_vector_pool_is_wider_than_the_answer(config: LocateConfig) -> None:
    session = _Recorder()
    locator = Locator(config, session=lambda: None, embed=lambda _q: None)  # type: ignore[arg-type,return-value]

    locator.match_vector(session, ["Occupation"], [0.0], [])  # type: ignore[arg-type]

    assert session.params["limit"] == config.search_limit
    assert session.params["k"] >= 8 * config.search_limit


def test_the_kind_filter_runs_after_the_pool() -> None:
    cypher = Locator(ONET_LOCATE, session=lambda: None, embed=lambda _q: None).q.vector  # type: ignore[arg-type,return-value]
    assert cypher.index("queryNodes($index, $k") < cypher.index("WHERE x IN $labels")
    assert cypher.index("WHERE x IN $labels") < cypher.index("LIMIT $limit")
