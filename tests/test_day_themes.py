from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest

from app.api.history_routes import _project_tip_enrichment
from app.planning.day_themes import build_day_theme, project_legacy_day_themes
from app.planning.restaurant_enrichment import route_fingerprint


@pytest.mark.parametrize(("names", "expected"), [
    (["上海城隍庙", "上海失恋博物馆"], "老城人文漫游"),
    (["上海朱家角古镇旅游区", "上海大观园"], "古镇园林寻幽"),
    (["上海动物园", "七宝老街"], "自然寻趣·老街慢游"),
    (["南京博物院"], "博物寻知"),
    (["上海动物园"], "自然寻趣"),
    (["未知景点"], "城市漫游"),
    ([], "自由探索"),
])
def test_daily_themes_summarize_attractions(names, expected):
    assert build_day_theme([{"name": name} for name in names]) == expected


def test_theme_considers_more_than_the_first_two_stops_and_ignores_duplicates():
    spots = [{"name": name} for name in ["市民广场", "中心步行街", "历史博物馆", "艺术博物院", "民俗博物馆"]]
    # Three museums outweigh the two generic urban stops.
    assert build_day_theme(spots) == "博物寻知·城市漫游"
    assert build_day_theme(spots + [spots[0]] * 5) == build_day_theme(spots)


def test_uses_poi_semantics_when_name_does_not_describe_the_place():
    assert build_day_theme([{"name": "红砖空间", "poi_type": "文化场馆;美术馆"}]) == "艺术漫游"
    assert build_day_theme([{"name": "探索中心", "semantic_tags": ["science"]}]) == "科学探索"
    assert build_day_theme([{"name": "上海动物园", "poi_type": "公园"}]) == "自然寻趣"


def test_history_projection_preserves_authored_titles_and_explicit_overrides():
    spots = [{"type": "attraction", "name": name} for name in ["城隍庙", "失恋博物馆"]]
    plan = {"day_themes": {"3": "城隍庙与失恋博物馆"}, "days": [
        {"day": 1, "theme": "城隍庙与失恋博物馆", "timeline": spots},
        {"day": 2, "theme": "古今与人文的对话", "timeline": spots},
        {"day": 3, "theme": "城隍庙与失恋博物馆", "timeline": spots},
        {"day": 4, "timeline": spots},
    ]}
    project_legacy_day_themes(plan)
    assert [day["theme"] for day in plan["days"]] == [
        "老城人文漫游", "古今与人文的对话", "城隍庙与失恋博物馆", "老城人文漫游",
    ]


def test_history_overlay_does_not_mutate_original_plan_or_route_fingerprint():
    data = {"id": "fixture", "plan": {"days": [{
        "day": 1, "theme": "上海动物园与七宝老街", "timeline": [
            {"type": "lunch", "name": "博物馆餐厅"},
            {"type": "attraction", "name": "上海动物园", "start_time": "09:00"},
            {"type": "attraction", "name": "七宝老街", "start_time": "14:00"},
        ],
    }]}}
    original = deepcopy(data)
    with patch("app.api.history_routes.load_tip_enrichment", return_value=None), patch(
        "app.api.history_routes._tip_run_status", return_value=None,
    ):
        projected = _project_tip_enrichment(data, "fixture-user", None)
    assert projected["plan"]["days"][0]["theme"] == "自然寻趣·老街慢游"
    assert data == original
    assert route_fingerprint(projected["plan"]) == route_fingerprint(data["plan"])
