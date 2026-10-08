from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.api.history_routes import _project_tip_enrichment
from app.core.database import configure_database, get_conn, get_db_path, init_db
from app.core.memory import load_itinerary, save_itinerary, update_plan_json
from app.planning.nodes import route_after_quality_gate
from app.planning import nodes
from app.planning.restaurant_enrichment import route_fingerprint
from app.planning.schemas import TravelPlanState
from app.planning.tip_enrichment import load_tip_enrichment, save_tip_enrichment
from app.planning.tip_enrichment import generate_tip_enrichment


def _plan(name: str = "钟山风景区") -> dict:
    return {
        "destination": "南京",
        "days": [{"day": 1, "timeline": [{"type": "attraction", "name": name}]}],
    }


def test_quality_gate_reaches_finalize_without_spot_tips_node():
    state = TravelPlanState(query="南京一日游", quality_report={"passed": True})
    assert route_after_quality_gate(state) == "finalize"


def test_spot_tips_use_a_single_non_thinking_structured_model(monkeypatch):
    monkeypatch.delenv("PLANNING_SPOT_TIPS_MODEL", raising=False)
    monkeypatch.delenv("PLANNING_AGENT_MODEL", raising=False)
    with patch("app.planning.nodes.build_structured_llm", return_value=object()) as build:
        nodes.make_spot_tips_node(None)

    build.assert_called_once_with(
        nodes.SpotTipsResult,
        model=None,
        temperature=0,
        thinking=False,
    )


def test_tip_projection_is_versioned_by_route_fingerprint():
    original_path = get_db_path()
    with tempfile.TemporaryDirectory() as tmp:
        configure_database(Path(tmp) / "tips.db")
        init_db()
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO users(id,username,password_hash,created_at) VALUES('u','u','x','now')"
                )
                plan_id = save_itinerary("u", _plan(), "南京一日游", conn)
                data = load_itinerary(plan_id, conn)
                fingerprint = route_fingerprint(data["plan"])
                save_tip_enrichment(plan_id, "u", fingerprint, {"钟山风景区": "建议穿舒适鞋"}, conn)
                projected = _project_tip_enrichment(data, "u", conn)
                assert projected["plan"]["tip_status"] == "succeeded"
                assert projected["plan"]["days"][0]["timeline"][0]["tip"] == "建议穿舒适鞋"

                changed = _plan("总统府")
                assert update_plan_json(plan_id, "u", changed, conn, expected_lock_version=1)
                current = load_itinerary(plan_id, conn)
                stale = _project_tip_enrichment(current, "u", conn)
                assert stale["plan"]["tip_status"] == "unavailable"
                assert stale["plan"]["days"][0]["timeline"][0]["tip"] is None
                assert load_tip_enrichment(plan_id, "u", route_fingerprint(current["plan"]), conn) is None
        finally:
            configure_database(original_path)


def test_empty_tip_payload_is_a_retryable_enrichment_failure():
    async def empty_node(_state):
        return {"spot_tips": {}}

    with patch("app.planning.tip_enrichment.make_spot_tips_node", return_value=empty_node):
        with pytest.raises(RuntimeError, match="no usable attraction tips"):
            asyncio.run(generate_tip_enrichment(_plan(), {"query": "南京一日游"}))
