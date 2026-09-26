from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.core.database as database

    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "history.db")
    database.init_db()
    from app.main import app
    return TestClient(app)


def _headers_and_history(count=7):
    from app.core.auth import create_token
    from app.core.database import get_conn
    from app.core.memory import save_itinerary

    user_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
            (user_id, f"history-{user_id}", "test", "2026-01-01T00:00:00Z"),
        )
        for index in range(count):
            itinerary_id = save_itinerary(
                user_id, {"destination": f"城市{index}"}, f"城市{index}旅行", conn
            )
            conn.execute(
                "UPDATE itineraries SET created_at=? WHERE id=?",
                (f"2026-08-{index + 1:02d}T00:00:00+00:00", itinerary_id),
            )
    return {"Authorization": "Bearer " + create_token(user_id)}


def test_history_cursor_pagination_is_stable_and_keeps_legacy_response(client):
    headers = _headers_and_history()

    first = client.get("/api/history?limit=3", headers=headers)
    assert first.status_code == 200
    first_body = first.json()
    assert [item["destination"] for item in first_body["items"]] == ["城市6", "城市5", "城市4"]
    assert first_body["next_cursor"]

    second = client.get(
        "/api/history", params={"limit": 3, "cursor": first_body["next_cursor"]}, headers=headers
    )
    assert second.status_code == 200
    second_body = second.json()
    assert [item["destination"] for item in second_body["items"]] == ["城市3", "城市2", "城市1"]
    assert not {item["id"] for item in first_body["items"]} & {item["id"] for item in second_body["items"]}

    last = client.get(
        "/api/history", params={"limit": 3, "cursor": second_body["next_cursor"]}, headers=headers
    )
    assert [item["destination"] for item in last.json()["items"]] == ["城市0"]
    assert last.json()["next_cursor"] is None

    legacy = client.get("/api/history", headers=headers)
    assert isinstance(legacy.json(), list)
    assert len(legacy.json()) == 7


def test_history_cursor_requires_auth_and_rejects_invalid_values(client):
    assert client.get("/api/history?limit=6").status_code == 401
    headers = _headers_and_history(1)
    assert client.get("/api/history?limit=6&cursor=bad", headers=headers).status_code == 400


def test_history_detail_displays_daily_theme_without_rewriting_saved_route(client):
    from app.core.database import get_conn
    from app.core.memory import load_itinerary, update_plan_json

    headers = _headers_and_history(1)
    plan_id = client.get("/api/history", headers=headers).json()[0]["id"]
    plan = {"destination": "上海", "days": [{
        "day": 1, "theme": "上海动物园与七宝老街", "timeline": [
            {"type": "attraction", "name": "上海动物园"},
            {"type": "attraction", "name": "七宝老街"},
        ],
    }]}
    with get_conn() as conn:
        user_id = conn.execute("SELECT user_id FROM itineraries WHERE id=?", (plan_id,)).fetchone()[0]
        assert update_plan_json(plan_id, user_id, plan, conn, expected_lock_version=1)
    response = client.get(f"/api/history/{plan_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["plan"]["days"][0]["theme"] == "自然寻趣·老街慢游"
    with get_conn() as conn:
        assert load_itinerary(plan_id, conn)["plan"] == plan
    assert client.get(f"/api/history/{plan_id}").status_code == 401
