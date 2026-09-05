from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.chat.models import DialogueDecision, DialogueTarget
from app.chat.service import ChatService
from app.core.database import get_conn, init_db
from app.core.memory import save_itinerary
from app.runtime.manager import RunManager
from app.runtime.models import RunKind, RunStatus
from app.runtime.repositories import ConversationRepository


class DialogueActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "dialogue.db"
        init_db(self.db_path)
        with get_conn(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                [
                    ("owner", "owner", "hash", "2026-01-01"),
                    ("other", "other", "hash", "2026-01-01"),
                ],
            )
        self.manager = RunManager(self.db_path)
        self.conversations = ConversationRepository(self.db_path)
        self.conversation = self.conversations.create("owner", "测试")
        self.service = ChatService(self.manager, self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _chat_run(self, text="帮我规划南京旅行"):
        _message, run = await self.service.submit_message(
            "owner", self.conversation["id"], text
        )
        return run

    async def test_brief_patch_normalizes_days_and_persists_assistant_reply(self):
        run = await self._chat_run()
        await self.service.actions.execute(
            run,
            DialogueDecision(
                intent="create_plan",
                reply="我先整理南京的三日行程。",
                brief_patch={
                    "destination": "南京",
                    "start_date": "2026-07-24",
                    "end_date": "2026-07-26",
                    "days": 2,
                    "trip_focus": "sights_first",
                },
            ),
        )
        brief = self.service.briefs.active_for_conversation("owner", self.conversation["id"])
        self.assertEqual(brief["status"], "ready")
        self.assertEqual(brief["data"]["days"], 3)
        messages = self.conversations.messages("owner", self.conversation["id"])
        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertEqual(messages[-1]["content"], "我先整理南京的三日行程。")

    async def test_start_date_and_duration_derive_end_date(self):
        """A complete relative-date request stays ready if the model omits end_date."""
        run = await self._chat_run("明天去南京3日游")
        await self.service.actions.execute(
            run,
            DialogueDecision(
                intent="create_plan",
                reply="已记下从明天开始的南京三日游。",
                brief_patch={
                    "destination": "南京",
                    "start_date": "2026-08-17",
                    "days": 3,
                    "trip_focus": "sights_first",
                },
            ),
        )
        brief = self.service.briefs.active_for_conversation("owner", self.conversation["id"])
        self.assertEqual(brief["status"], "ready")
        self.assertEqual(brief["data"]["start_date"], "2026-08-17")
        self.assertEqual(brief["data"]["end_date"], "2026-08-19")
        self.assertEqual(brief["data"]["days"], 3)

    async def test_invalid_date_has_no_brief_or_follow_up_run_side_effect(self):
        run = await self._chat_run()
        before = self.manager.runs.list("owner", conversation_id=self.conversation["id"])
        await self.service.actions.execute(
            run,
            DialogueDecision(
                intent="create_plan",
                reply="已记录。",
                brief_patch={"destination": "南京", "start_date": "bad-date"},
            ),
        )
        self.assertIsNone(self.service.briefs.active_for_conversation("owner", self.conversation["id"]))
        after = self.manager.runs.list("owner", conversation_id=self.conversation["id"])
        self.assertEqual([item["id"] for item in after], [item["id"] for item in before])
        messages = self.conversations.messages("owner", self.conversation["id"])
        self.assertIn("日期范围", messages[-1]["content"])

    async def test_confirm_ready_brief_is_idempotent(self):
        run = await self._chat_run()
        brief = await self.service.apply_brief_patch(
            run,
            {"destination": "南京", "start_date": "2026-07-24", "end_date": "2026-07-26", "trip_focus": "sights_first"},
        )
        with patch.object(
            self.service,
            "execute_planner_tool",
            new=AsyncMock(return_value=(brief, "itinerary-inline")),
        ):
            first = await self.service.actions.execute(
                run, DialogueDecision(intent="confirm_plan", reply="现在开始规划。")
            )
            second = await self.service.actions.execute(
                run, DialogueDecision(intent="confirm_plan", reply="再确认一次。")
            )
        runs = self.manager.runs.list("owner", conversation_id=self.conversation["id"])
        self.assertEqual(len([item for item in runs if item["kind"] == RunKind.TRAVEL_PLAN.value]), 0)
        self.assertEqual(first["result_itinerary_id"], "itinerary-inline")
        self.assertEqual(second["result_itinerary_id"], "itinerary-inline")

    async def test_bound_chat_message_executes_revision_as_inline_task(self):
        with get_conn(self.db_path) as conn:
            itinerary_id = save_itinerary(
                "owner",
                {"destination": "南京", "days": []},
                "南京旅行",
                conn,
                planner_state={"query": "南京旅行", "route": [], "pois": []},
            )
        _message, run = await self.service.submit_message(
            "owner",
            self.conversation["id"],
            "第二天安排轻松一点",
            related_itinerary_id=itinerary_id,
        )

        with patch.object(
            self.service,
            "execute_revision_tool",
            new=AsyncMock(return_value=("itinerary-revised", None)),
        ) as execute_revision:
            result = await self.service.actions.execute(
                run,
                DialogueDecision(
                    intent="modify_itinerary",
                    reply="已经开始修改。",
                    target=DialogueTarget(itinerary_id=itinerary_id),
                    modification_notes="第二天安排轻松一点",
                ),
            )

        execute_revision.assert_awaited_once_with(
            run, itinerary_id, "第二天安排轻松一点"
        )
        self.assertEqual(result["result_itinerary_id"], "itinerary-revised")
        runs = self.manager.runs.list("owner", conversation_id=self.conversation["id"])
        self.assertEqual(len([item for item in runs if item["kind"] == RunKind.REVISION.value]), 0)

    async def test_control_requires_structured_action_and_valid_target_state(self):
        run = await self._chat_run("把任务停掉")
        target = self.manager.create(
            user_id="owner", kind=RunKind.TRAVEL_PLAN,
            conversation_id=self.conversation["id"], request_snapshot={"destination": "南京"},
        )
        await self.service.actions.execute(
            run,
            DialogueDecision(
                intent="run_control", reply="好的。", target=DialogueTarget(run_id=target["id"]),
                run_action="cancel", requires_confirmation=True,
            ),
        )
        self.assertEqual(self.manager.runs.get_internal(target["id"])["status"], RunStatus.QUEUED.value)
        await self.service.actions.execute(
            run,
            DialogueDecision(
                intent="run_control", reply="已停止。", target=DialogueTarget(run_id=target["id"]),
                run_action="cancel",
            ),
        )
        self.assertEqual(self.manager.runs.get_internal(target["id"])["status"], RunStatus.CANCELLED.value)

    async def test_free_text_reply_cannot_confirm_or_control_anything(self):
        run = await self._chat_run("确认并停止")
        target = self.manager.create(
            user_id="owner", kind=RunKind.TRAVEL_PLAN,
            conversation_id=self.conversation["id"], request_snapshot={"destination": "南京"},
        )
        await self.service.actions.execute(
            run,
            DialogueDecision(
                intent="general_chat",
                reply="我已经确认并停止了任务。",
            ),
        )
        self.assertEqual(self.manager.runs.get_internal(target["id"])["status"], RunStatus.QUEUED.value)
        self.assertIsNone(self.service.briefs.active_for_conversation("owner", self.conversation["id"]))

    async def test_chat_context_is_bounded_and_excludes_other_user_resources(self):
        for index in range(15):
            self.conversations.add_message(
                "owner", self.conversation["id"], "assistant", f"历史 {index}"
            )
        _message, run = await self.service.submit_message(
            "owner", self.conversation["id"], "当前消息"
        )
        other = self.conversations.create("other", "隔离")
        self.manager.create(
            user_id="other", kind=RunKind.TRAVEL_PLAN,
            conversation_id=other["id"], request_snapshot={"destination": "不应出现"},
        )
        context = (await self.service.chat_input(run))["dialogue_context"]
        self.assertEqual(context["today"].count("-"), 2)
        self.assertEqual(context["timezone"], "Asia/Shanghai")
        self.assertEqual(context["current_message"], "当前消息")
        self.assertLessEqual(len(context["history"]), 15)
        self.assertEqual(context["history"][-1]["content"], "历史 14")
        self.assertNotIn("不应出现", str(context))
