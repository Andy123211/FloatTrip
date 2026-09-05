from __future__ import annotations

import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult

from app.chat.models import MainAgentContext
from app.chat.service import ChatService
from app.chat.react_graph import build_main_agent_graph
from app.chat.tool_service import MainAgentToolService
from app.chat.tools import build_main_agent_tools
from app.core.database import get_conn, init_db
from app.core.memory import save_itinerary
from app.core.travel_memory import MemoryRepository
from app.runtime.manager import RunManager
from app.runtime.repositories import ConversationRepository
from app.runtime.worker import GraphRuntimeWorker
from app.planning.schemas import TravelPlanState


class ToolCapableFakeModel(BaseChatModel):
    responses: list[AIMessage]
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "tool-capable-fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.responses[self.index]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


class MainAgentToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "agent.db"
        init_db(self.db_path)
        with get_conn(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                [("owner", "owner", "hash", "2026-01-01"),
                 ("other", "other", "hash", "2026-01-01")],
            )
        self.manager = RunManager(self.db_path)
        self.service = ChatService(self.manager, self.db_path)
        self.conversations = ConversationRepository(self.db_path)
        self.memory = MemoryRepository(self.db_path)
        self.conversation = self.conversations.create("owner", "南京")

        self.memory.create(
            "owner", category="travel_pace", value_text="喜欢慢节奏",
            polarity="prefer", status="active", source_kind="manual",
        )
        self.memory.create(
            "owner", category="destination_history", value_text="苏州",
            polarity="fact", status="active", source_kind="manual",
        )
        self.memory.create(
            "owner", category="destination_history", value_text="杭州",
            polarity="fact", status="candidate", source_kind="inferred_chat",
        )
        _message, self.run = await self.service.submit_message(
            "owner", self.conversation["id"], "把我的旅行信息找出来"
        )
        self.context = await self.service.main_agent_context(self.run)
        self.tools = MainAgentToolService(self.service, self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_public_tool_schemas_never_expose_identity(self):
        forbidden = {"user_id", "conversation_id", "chat_run_id", "memory_revision", "runtime"}
        registered = build_main_agent_tools(self.tools)
        self.assertEqual(len(registered), 8)
        for tool in registered:
            self.assertFalse(forbidden.intersection(tool.args), tool.name)

    def test_memory_reads_only_frozen_active_snapshot(self):
        result = self.tools.get_travel_memory(
            self.context, categories=["destination_history"], limit=10
        )
        self.assertEqual([fact["value_text"] for fact in result["facts"]], ["苏州"])
        self.memory.create(
            "owner", category="destination_history", value_text="南京",
            polarity="fact", status="active", source_kind="manual",
        )
        still_frozen = self.tools.get_travel_memory(
            self.context, categories=["destination_history"], limit=10
        )
        self.assertEqual([fact["value_text"] for fact in still_frozen["facts"]], ["苏州"])

    def test_nanjing_three_day_search_deduplicates_latest_revision_and_owner(self):
        plan = {
            "destination": "南京", "start_date": "2026-10-01", "end_date": "2026-10-03",
            "days": [{"day": 1, "activities": [{"name": "中山陵"}]},
                     {"day": 2, "activities": [{"name": "南京博物院"}]},
                     {"day": 3, "activities": [{"name": "夫子庙"}]}],
        }
        with get_conn(self.db_path) as conn:
            original = save_itinerary("owner", plan, "南京3日游", conn, planner_state={"ok": True})
            revised = save_itinerary(
                "owner", plan, "南京3日游", conn, parent_id=original,
                modification_notes="第三天轻松一点", planner_state={"ok": True},
            )
            save_itinerary("other", plan, "南京3日游", conn, planner_state={"ok": True})
        result, artifact = self.tools.find_saved_itineraries(
            self.context, destination="南京", duration_days=3
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["exact_matches"][0]["itinerary_id"], revised)
        self.assertEqual(artifact["items"][0]["version"], 2)
        self.assertTrue(artifact["items"][0]["is_modified"])
        self.assertNotIn("plan_json", str(artifact))

    def test_itinerary_summary_is_bounded_but_one_day_can_be_read(self):
        plan = {
            "destination": "南京", "start_date": "2026-10-01", "end_date": "2026-10-03",
            "days": [{"day": day, "activities": [{"name": f"景点{day}"}]} for day in range(1, 4)],
        }
        with get_conn(self.db_path) as conn:
            itinerary_id = save_itinerary("owner", plan, "南京3日游", conn)
        summary = self.tools.get_saved_itinerary(self.context, itinerary_id)
        self.assertNotIn("day", summary)
        self.assertNotIn("days", summary)
        selected = self.tools.get_saved_itinerary(self.context, itinerary_id, 2)
        self.assertEqual(selected["day"]["day"], 2)

    def test_itinerary_search_separates_near_no_result_and_five_card_limit(self):
        four_day = {
            "destination": "南京", "start_date": "2026-10-01", "end_date": "2026-10-04",
            "days": [{"day": day, "activities": []} for day in range(1, 5)],
        }
        with get_conn(self.db_path) as conn:
            for index in range(7):
                save_itinerary("owner", four_day, f"南京方案 {index}", conn)
        near, artifact = self.tools.find_saved_itineraries(
            self.context, destination="南京", duration_days=3, limit=5
        )
        self.assertEqual(near["exact_matches"], [])
        self.assertEqual(len(near["near_matches"]), 5)
        self.assertEqual(artifact["match_kind"], "near")
        self.assertEqual(len(artifact["items"]), 5)
        missing, missing_artifact = self.tools.find_saved_itineraries(
            self.context, destination="拉萨", duration_days=3
        )
        self.assertEqual(missing["count"], 0)
        self.assertIsNone(missing_artifact)

    async def test_brief_submission_through_tool_service_is_idempotent(self):
        updated = await self.tools.update_current_brief(self.context, {
            "destination": "南京", "start_date": "2026-10-01", "end_date": "2026-10-03",
            "trip_focus": "sights_first",
        })
        self.assertTrue(updated.ok)
        brief = self.service.briefs.active_for_conversation("owner", self.conversation["id"])
        with patch.object(
            self.service, "execute_planner_tool",
            new=AsyncMock(return_value=(brief, "itinerary-inline")),
        ):
            first = await self.tools.submit_current_brief(self.context)
            second = await self.tools.submit_current_brief(self.context)
        self.assertTrue(first.ok)
        self.assertEqual(first.data["itinerary_id"], second.data["itinerary_id"])
        runs = self.service.runs.list("owner", conversation_id=self.conversation["id"])
        self.assertEqual(len([run for run in runs if run["kind"] == "travel_plan"]), 0)

    async def test_planner_task_accepts_pydantic_values_and_hides_ready_brief(self):
        brief = await self.service.apply_brief_patch(self.run, {
            "destination": "南京", "start_date": "2026-10-01",
            "end_date": "2026-10-03", "trip_focus": "sights_first",
        })

        class FakePlannerGraph:
            async def astream(self, *_args, **_kwargs):
                yield {
                    "type": "values",
                    "data": TravelPlanState(
                        query="南京3日游",
                        final_plan={"destination": "南京", "days": []},
                    ),
                }

        finalizer = AsyncMock(return_value={"result_itinerary_id": "itinerary-inline"})
        with patch("app.planning.graph.build_graph", return_value=FakePlannerGraph()), patch(
            "app.planning.runtime_worker.PlanningFinalizer", return_value=finalizer
        ):
            submitted, itinerary_id = await self.service.execute_planner_tool(
                self.run, brief["id"]
            )

        self.assertEqual(itinerary_id, "itinerary-inline")
        self.assertEqual(submitted["status"], "submitted")
        finalizer_updates = finalizer.await_args.args[1]
        self.assertEqual(finalizer_updates["final_plan"]["destination"], "南京")
        events = self.manager.events.after(self.run["id"])
        self.assertTrue(any(
            item["payload"].get("kind") == "planning_brief.submitted"
            for item in events
        ))

    async def test_assistant_message_round_trips_tool_artifact(self):
        artifact = {
            "type": "itinerary_collection", "title": "南京方案", "match_kind": "exact",
            "items": [{
                "itinerary_id": "it-1", "root_id": "it-1", "version": 1,
                "destination": "南京", "duration_days": 3,
                "start_date": None, "end_date": None, "created_at": "2026-01-01",
                "is_modified": False, "highlights": ["中山陵"],
            }],
        }
        await self.service.finalize_react_chat(
            self.run,
            {"messages": [
                ToolMessage(content="找到 1 个方案", tool_call_id="call-1", artifact=artifact),
                AIMessage(content="找到了你的南京三日游。"),
            ]},
            "",
        )
        rows = self.conversations.messages("owner", self.conversation["id"])
        self.assertEqual(rows[-1]["artifacts"][0]["items"][0]["itinerary_id"], "it-1")
        events = self.manager.events.after(self.run["id"])
        completed = next(item for item in events if item["payload"].get("kind") == "chat.message.completed")
        self.assertEqual(completed["payload"]["artifacts"], rows[-1]["artifacts"])

    async def test_stable_memory_prefix_ignores_dynamic_brief_changes(self):
        first = await self.service.react_chat_input(self.run)
        await self.service.apply_brief_patch(self.run, {"destination": "南京", "days": 3})
        second = await self.service.react_chat_input(self.run)
        self.assertEqual(first["messages"][0].content, second["messages"][0].content)
        self.assertNotIn("planning_brief", first["messages"][0].content)

    def test_tool_lifecycle_projection_is_sanitized_and_correlated(self):
        known = {}
        started = GraphRuntimeWorker._safe_tool_activity(
            {"type": "tool_call", "name": "find_saved_itineraries", "id": "abc", "args": {"secret": "x"}},
            known,
        )
        completed = GraphRuntimeWorker._safe_tool_activity(
            {"type": "tool_result", "id": "abc", "result": {"private": "x"}}, known
        )
        self.assertEqual(started["activity_id"], completed["activity_id"])
        self.assertEqual(started["kind"], "agent.activity.started")
        self.assertEqual(completed["kind"], "agent.activity.completed")
        self.assertNotIn("secret", str(started))
        self.assertNotIn("private", str(completed))

    async def test_react_worker_streams_safe_tool_lifecycle_and_persists_final_reply(self):
        model = ToolCapableFakeModel(responses=[
            AIMessage(content="", tool_calls=[{
                "name": "get_planning_context", "args": {},
                "id": "call-context", "type": "tool_call",
            }]),
            AIMessage(content="当前还没有完整的旅行需求。"),
        ])
        worker = GraphRuntimeWorker(
            self.manager,
            build_main_agent_graph(build_main_agent_tools(self.tools), llm=model),
            self.service.react_chat_input,
            stream_messages=True,
            stream_tools=True,
            visible_nodes={"model"},
            context_builder=self.service.main_agent_context,
            finalizer=self.service.finalize_react_chat,
        )
        await worker(self.run, asyncio.Event())
        events = self.manager.events.after(self.run["id"])
        activities = [
            item["payload"] for item in events
            if str(item["payload"].get("kind", "")).startswith("agent.activity.")
        ]
        self.assertEqual(
            [item["kind"] for item in activities],
            ["agent.activity.started", "agent.activity.completed"],
        )
        self.assertEqual(activities[0]["activity_id"], activities[1]["activity_id"])
        self.assertNotIn("get_planning_context", str(activities))
        messages = self.conversations.messages("owner", self.conversation["id"])
        self.assertEqual(messages[-1]["content"], "当前还没有完整的旅行需求。")


if __name__ == "__main__":
    unittest.main()
