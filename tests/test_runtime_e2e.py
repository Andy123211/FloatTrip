from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.chat.service import ChatService
from app.chat.models import DialogueDecision, DialogueTarget
from app.core.database import get_conn, init_db
from app.core.memory import load_itinerary, save_itinerary
from app.api.history_routes import _project_tip_enrichment
from app.planning.runtime_worker import PlanningFinalizer, SpotTipsWorker
from app.runtime.manager import RunManager
from app.runtime.models import RunKind
from app.runtime.repositories import ConversationRepository
from app.runtime.scheduler import RuntimeScheduler


class RuntimeEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "e2e.db"
        init_db(self.db_path)
        with get_conn(self.db_path) as conn:
            conn.execute(
                "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                ("owner", "owner", "hash", "2026-01-01"),
            )
        self.manager = RunManager(self.db_path)
        self.scheduler = RuntimeScheduler(
            self.manager,
            planning_limit=2,
            planning_per_user=2,
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _wait_terminal(self, run_id: str):
        for _ in range(100):
            run = self.manager.runs.get_internal(run_id)
            if run["status"] in {"succeeded", "failed", "cancelled"}:
                return run
            await asyncio.sleep(0.01)
        self.fail(f"run {run_id} did not finish")

    async def test_chat_to_brief_confirmation_to_itinerary(self):
        conversations = ConversationRepository(self.db_path)
        conversation = conversations.create("owner", "云南")
        service = ChatService(self.manager, self.db_path)
        _message, chat_run = await service.submit_message(
            "owner", conversation["id"], "帮我规划云南五日游"
        )
        brief = await service.apply_brief_patch(
            chat_run,
            {
                "destination": "云南",
                "days": 5,
                "start_date": "2026-10-01",
                "end_date": "2026-10-05",
                "trip_focus": "sights_first",
            },
        )
        self.assertEqual(brief["status"], "ready")
        submitted, planning_run = await service.submit_brief(
            "owner", brief["id"], origin_chat_run_id=chat_run["id"]
        )
        self.assertEqual(submitted["status"], "submitted")

        async def planning_handler(run, _cancel):
            with get_conn(self.db_path) as conn:
                itinerary_id = save_itinerary(
                    run["user_id"],
                    {"destination": "云南", "days": []},
                    run["request_snapshot"]["query"],
                    conn,
                )
            return {"result_itinerary_id": itinerary_id}

        self.scheduler.register(RunKind.TRAVEL_PLAN, planning_handler)
        await self.scheduler.start()
        finished = await self._wait_terminal(planning_run["id"])
        await self.scheduler.stop()
        self.assertEqual(finished["status"], "succeeded")
        self.assertTrue(finished["result_itinerary_id"])
        self.assertEqual(submitted["submission_snapshot"]["destination"], "云南")
        self.assertEqual(submitted["submission_snapshot"]["days"], 5)
        self.assertEqual(
            [item["value_text"] for item in submitted["submission_snapshot"]["effective_constraints"]],
            ["景点为主，餐饮仅作就近用餐"],
        )
        self.assertEqual(
            submitted["submission_snapshot"]["constraint_coverage"][0]["status"],
            "applied",
        )

    async def test_targeted_message_runs_revision_inside_the_chat_run(self):
        conversations = ConversationRepository(self.db_path)
        conversation = conversations.create("owner", "泉州")
        with get_conn(self.db_path) as conn:
            itinerary_id = save_itinerary(
                "owner",
                {"destination": "泉州", "days": []},
                "泉州四日游",
                conn,
                planner_state={"query": "泉州四日游", "route": [], "pois": []},
            )
        service = ChatService(self.manager, self.db_path)

        message, run = await service.submit_message(
            "owner",
            conversation["id"],
            "第三天下午不要安排景点，留给我休息；其余不变。",
            related_itinerary_id=itinerary_id,
        )

        self.assertEqual(message["related_itinerary_id"], itinerary_id)
        self.assertEqual(run["kind"], "chat")
        self.assertEqual(run["conversation_id"], conversation["id"])
        self.assertEqual(
            run["request_snapshot"]["related_itinerary_id"],
            itinerary_id,
        )
        self.assertEqual(run["request_snapshot"]["text"], "第三天下午不要安排景点，留给我休息；其余不变。")

        with patch.object(
            service,
            "execute_revision_tool",
            new=AsyncMock(return_value=("itinerary-revised", None)),
        ) as execute_revision:
            result = await service.actions.execute(
                run,
                DialogueDecision(
                    intent="modify_itinerary",
                    reply="我会为第三天下午留出休息时间。",
                    target=DialogueTarget(itinerary_id=itinerary_id),
                    modification_notes="第三天下午不要安排景点，留给我休息；其余不变。",
                ),
            )
        execute_revision.assert_awaited_once_with(
            run, itinerary_id, "第三天下午不要安排景点，留给我休息；其余不变。"
        )
        self.assertEqual(result["result_itinerary_id"], "itinerary-revised")
        runs = self.manager.runs.list("owner", conversation_id=conversation["id"])
        self.assertEqual(len([item for item in runs if item["kind"] == "revision"]), 0)

    async def test_two_plans_execute_concurrently_without_merging(self):
        active = 0
        maximum = 0
        snapshots = []

        async def planning_handler(run, _cancel):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            snapshots.append(run["request_snapshot"]["destination"])
            await asyncio.sleep(0.03)
            active -= 1
            return {}

        self.scheduler.register(RunKind.TRAVEL_PLAN, planning_handler)
        first = self.manager.create(
            user_id="owner",
            kind=RunKind.TRAVEL_PLAN,
            request_snapshot={"destination": "云南", "days": 5},
        )
        second = self.manager.create(
            user_id="owner",
            kind=RunKind.TRAVEL_PLAN,
            request_snapshot={"destination": "新疆", "days": 7},
        )
        await self.scheduler.start()
        await asyncio.gather(
            self._wait_terminal(first["id"]),
            self._wait_terminal(second["id"]),
        )
        await self.scheduler.stop()
        self.assertEqual(maximum, 2)
        self.assertCountEqual(snapshots, ["云南", "新疆"])

    async def test_itinerary_is_available_before_async_tips_then_receives_sse_update(self):
        """The core planning run must succeed without waiting for tip enrichment."""
        plan = {
            "destination": "南京",
            "start_date": "2026-08-17",
            "end_date": "2026-08-17",
            "days": [{
                "day": 1,
                "timeline": [{"type": "attraction", "name": "钟山风景区", "period": "morning"}],
            }],
        }
        tip_started = asyncio.Event()
        release_tips = asyncio.Event()

        async def generate_tips(_plan, _state):
            tip_started.set()
            await release_tips.wait()
            return {"钟山风景区": "建议预留半天并穿舒适步行鞋"}

        finalizer = PlanningFinalizer(self.manager, self.scheduler.notify)

        async def planning_handler(run, _cancel):
            return await finalizer(run, {"final_plan": plan}, "")

        primary = self.manager.create(
            user_id="owner",
            kind=RunKind.TRAVEL_PLAN,
            request_snapshot={"query": "南京一日游", "destination": "南京", "days": 1},
        )
        self.scheduler.register(RunKind.TRAVEL_PLAN, planning_handler)
        self.scheduler.register(RunKind.SPOT_TIPS, SpotTipsWorker(self.manager))

        # Finalizer/worker use the application connection helper; bind it to
        # this isolated e2e database rather than the developer's local DB.
        with patch("app.planning.runtime_worker.get_conn", lambda: get_conn(self.db_path)), \
             patch("app.planning.runtime_worker.launch_shadow_profiles") as shadow, \
             patch("app.planning.runtime_worker.generate_tip_enrichment", generate_tips):
            await self.scheduler.start()
            self.scheduler.notify()
            finished = await self._wait_terminal(primary["id"])
            self.assertEqual(finished["status"], "succeeded")
            itinerary_id = finished["result_itinerary_id"]
            self.assertTrue(itinerary_id)
            shadow.assert_called_once()

            # The background task is deliberately blocked, but the itinerary
            # already exists and projects a non-success tip state.
            await asyncio.wait_for(tip_started.wait(), timeout=1)
            with get_conn(self.db_path) as conn:
                pending = _project_tip_enrichment(load_itinerary(itinerary_id, conn), "owner", conn)
            self.assertIn(pending["plan"]["tip_status"], {"queued", "running"})

            async with self.manager.bridge.subscribe(f"itinerary:{itinerary_id}") as stream:
                release_tips.set()
                events = []
                async for item in stream:
                    events.append(item.payload)
                    if item.payload.get("status") == "succeeded":
                        break
            tip_runs = [r for r in self.manager.runs.queued() if r["kind"] == RunKind.SPOT_TIPS.value]
            self.assertFalse(tip_runs)
            with get_conn(self.db_path) as conn:
                enriched = _project_tip_enrichment(load_itinerary(itinerary_id, conn), "owner", conn)
            await self.scheduler.stop()

        self.assertIn("succeeded", [event.get("status") for event in events])
        self.assertEqual(enriched["plan"]["tip_status"], "succeeded")
        self.assertEqual(
            enriched["plan"]["days"][0]["timeline"][0]["tip"],
            "建议预留半天并穿舒适步行鞋",
        )
