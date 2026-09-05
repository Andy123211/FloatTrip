from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.core.planning_constraints import (
    compatibility_preferences,
    constraint_directive,
    constraints_for_prompt,
    normalize_trip_constraint,
    planning_instruction,
)
from app.planning.nodes import finalize_node
from app.planning.nodes import make_candidate_builder_node
from app.planning.runtime_worker import snapshot_to_state
from app.planning.schemas import TravelPlanState


class PlanningConstraintPipelineTests(unittest.TestCase):
    def test_planning_instruction_preserves_confirmed_dates_and_constraints(self):
        instruction = planning_instruction({
            "destination": "南京", "start_date": "2026-08-17", "end_date": "2026-08-19", "days": 3,
            "effective_constraints": [
                {"category": "attraction_preference", "value_text": "夫子庙", "polarity": "avoid"},
                {"category": "travel_pace", "value_text": "不喜欢早起", "polarity": "prefer"},
            ],
        })
        self.assertIn("南京3日游", instruction)
        self.assertIn("2026-08-17至2026-08-19", instruction)
        self.assertIn("必须避开：夫子庙", instruction)
        self.assertIn("优先考虑：不喜欢早起", instruction)

    def test_snapshot_maps_dates_budget_and_structured_constraints(self):
        snapshot = {
            "query": "东京三日游",
            "destination": "东京",
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
            "days": 3,
            "trip_budget": "5000 元",
            "effective_constraints": [{
                "id": "memory:f1", "fact_id": "f1", "category": "dietary_requirement",
                "value_text": "避开花生", "polarity": "require", "source": "long_term_memory",
            }],
            "constraint_coverage": [{
                "constraint_id": "memory:f1", "fact_id": "f1",
                "category": "dietary_requirement", "status": "unverified",
                "stages": ["meal_search", "meal_recommend"],
            }],
        }
        state = snapshot_to_state(snapshot)
        self.assertEqual(state.travel_start_date.isoformat(), "2026-09-01")
        self.assertEqual(state.trip_budget, "5000 元")
        self.assertEqual(state.effective_constraints[0]["fact_id"], "f1")

    def test_snapshot_retains_main_agent_planning_instruction(self):
        state = snapshot_to_state({
            "query": "南京三日游",
            "planning_instruction": "为用户规划南京三日游；必须避开夫子庙。",
        })
        self.assertEqual(
            state.planning_instruction,
            "为用户规划南京三日游；必须避开夫子庙。",
        )

    def test_compatibility_projection_feeds_existing_planner_fields(self):
        fields = compatibility_preferences([
            {"category": "attraction_preference", "value_text": "喜欢博物馆", "polarity": "prefer"},
            {"category": "dietary_requirement", "value_text": "避开花生", "polarity": "require"},
            {"category": "accessibility_need", "value_text": "尽量少走楼梯", "polarity": "prefer"},
        ])
        self.assertEqual(fields["attraction_preference"], "优先考虑：喜欢博物馆")
        self.assertEqual(fields["food_preference"], "必须满足：避开花生")
        self.assertEqual(fields["habit_preference"], "优先考虑：尽量少走楼梯")

    def test_avoid_constraint_becomes_an_explicit_planning_directive(self):
        constraint = {
            "category": "attraction_preference", "value_text": "老门东",
            "polarity": "avoid", "source": "long_term_memory",
        }
        self.assertEqual(constraint_directive(constraint), "必须避开：老门东")
        self.assertIn("必须避开：老门东", constraints_for_prompt([constraint]))
        fields = compatibility_preferences([constraint])
        self.assertEqual(fields["attraction_preference"], "必须避开：老门东")

    def test_explicit_negative_text_overrides_an_incorrect_model_polarity(self):
        constraint = normalize_trip_constraint({
            "category": "attraction_preference", "value_text": "避开博物馆",
            "polarity": "prefer",
        })
        self.assertEqual(constraint["polarity"], "avoid")

    def test_final_plan_preserves_coverage_and_does_not_pretend_to_search_hotels(self):
        state = TravelPlanState(
            query="京都两日游", destination="京都", days=2,
            effective_constraints=[{
                "id": "memory:hotel", "fact_id": "hotel", "category": "accommodation_preference",
                "value_text": "偏好安静住宿", "polarity": "prefer", "source": "long_term_memory",
            }],
            constraint_coverage=[{
                "constraint_id": "memory:hotel", "fact_id": "hotel",
                "category": "accommodation_preference", "status": "advisory", "stages": ["finalize"],
            }],
        )
        plan = finalize_node(state)["final_plan"]
        self.assertEqual(plan["constraint_coverage"][0]["status"], "advisory")
        self.assertIn("未接入酒店搜索", plan["planning_notes"][0])
        self.assertNotIn("hotel", plan)

    def test_candidate_builder_timeout_falls_back_to_server_candidates(self):
        poi = {
            "name": "测试景点", "location": {"lng": 118.8, "lat": 32.0},
            "rating": 4.8, "type": "风景名胜", "open_time": "09:00-17:00",
        }
        state = TravelPlanState(query="测试", destination="南京", days=1, pois=[poi])
        with patch("app.planning.nodes.build_structured_llm"), patch(
            "app.planning.nodes.ainvoke_structured", new=AsyncMock(side_effect=TimeoutError)
        ):
            node = make_candidate_builder_node(None)
            result = __import__("asyncio").run(node(state))
        self.assertEqual(result["candidate_pool"][0]["poi_name"], "测试景点")
        self.assertIn("LLM_CANDIDATE_BUILDER_FAILED:TimeoutError", result["candidate_builder_warnings"])


if __name__ == "__main__":
    unittest.main()
