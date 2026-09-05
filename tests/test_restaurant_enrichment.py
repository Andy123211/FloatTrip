from __future__ import annotations

import copy
import uuid

import pytest
from fastapi.testclient import TestClient

from app.planning.restaurant_enrichment import (
    generate_restaurant_enrichment,
    route_fingerprint,
)


def plan():
    return {
        "destination": "南京",
        "days": [{
            "day": 1,
            "timeline": [
                {
                    "type": "attraction", "name": "博物馆",
                    "start_time": "09:00", "end_time": "11:00",
                    "location": {"lng": 118.78, "lat": 32.04},
                    "meal_scene": "none", "meal_coverage": None,
                },
                {
                    "type": "attraction", "name": "老门东小吃街",
                    "start_time": "18:00", "end_time": "19:30",
                    "location": {"lng": 118.79, "lat": 32.03},
                    "meal_scene": "either", "meal_coverage": "dinner",
                },
            ],
        }],
    }


def test_covered_meal_skips_external_search_and_uncovered_meal_searches():
    calls = []

    def search(location, key, **kwargs):
        calls.append((location, kwargs))
        return [{
            "name": "午餐馆", "location": "118.781,32.041",
            "type": "餐饮服务;中餐厅", "biz_ext": {"rating": "4.8"},
        }]

    result = generate_restaurant_enrichment(plan(), api_key="k", search_around=search)
    meals = result["days"][0]["meals"]
    lunch = next(item for item in meals if item["meal"] == "lunch")
    dinner = next(item for item in meals if item["meal"] == "dinner")
    assert lunch["external_restaurant"]["name"] == "午餐馆"
    assert dinner == {
        "meal": "dinner",
        "covered_by_attraction": "老门东小吃街",
        "external_restaurant": None,
        "food_street_suggestions": [],
    }
    assert len(calls) == 1


def test_route_fingerprint_ignores_metadata_but_changes_with_route():
    original = plan()
    metadata_edit = copy.deepcopy(original)
    metadata_edit["notes"] = "用户备注"
    assert route_fingerprint(metadata_edit) == route_fingerprint(original)
    route_edit = copy.deepcopy(original)
    route_edit["days"][0]["timeline"][0]["start_time"] = "09:30"
    assert route_fingerprint(route_edit) != route_fingerprint(original)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.core.database as database

    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "restaurants.db")
    database.init_db()
    from app.main import app

    return TestClient(app)


def test_api_persists_separate_enrichment_without_mutating_core_plan(client, monkeypatch):
    from app.core.auth import create_token
    from app.core.database import get_conn
    from app.core.memory import load_itinerary, save_itinerary
    import app.api.plan_routes as routes

    user_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
            (user_id, f"restaurant-{user_id}", "x", "2026-01-01T00:00:00Z"),
        )
        plan_id = save_itinerary(user_id, plan(), "南京一日游", conn)
    before = copy.deepcopy(plan())

    def fake_search(location, key, **kwargs):
        return [{
            "name": "午餐馆", "location": "118.781,32.041",
            "type": "餐饮服务;中餐厅", "biz_ext": {"rating": "4.8"},
        }]

    monkeypatch.setattr(routes, "search_around_pois", fake_search)
    headers = {"Authorization": "Bearer " + create_token(user_id)}
    response = client.post(
        f"/api/plan/{plan_id}/restaurant-enrichment",
        json={"refresh": False}, headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["enrichment"]["days"][0]["meals"][1]["external_restaurant"] is None
    with get_conn() as conn:
        assert load_itinerary(plan_id, conn)["plan"] == before
        count = conn.execute(
            "SELECT COUNT(*) FROM itinerary_enrichments WHERE itinerary_id=?", (plan_id,)
        ).fetchone()[0]
    assert count == 1

    cached = client.post(
        f"/api/plan/{plan_id}/restaurant-enrichment",
        json={"refresh": False}, headers=headers,
    )
    assert cached.json()["cached"] is True
