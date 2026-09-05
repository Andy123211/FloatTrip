from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from app.core.database import get_conn, init_db
from app.core.memory import save_itinerary
from app.runtime.models import RunKind, RunStatus, concurrency_key
from app.runtime.repositories import (
    ConversationRunActive,
    ConversationRepository,
    OwnedResourceNotFound,
    PlanningBriefRepository,
    RunEventRepository,
    RunRepository,
)


class RepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "runtime.db"
        init_db(self.db_path)
        with get_conn(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                [
                    ("user-a", "a", "hash", "2026-01-01T00:00:00Z"),
                    ("user-b", "b", "hash", "2026-01-01T00:00:00Z"),
                ],
            )
        self.conversations = ConversationRepository(self.db_path)
        self.briefs = PlanningBriefRepository(self.db_path)
        self.runs = RunRepository(self.db_path)
        self.events = RunEventRepository(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def create_run(self, user_id="user-a", conversation_id=None):
        run_id = f"run-{id(self)}-{conversation_id or 'none'}"
        with get_conn(self.db_path) as conn:
            return self.runs.insert(
                conn,
                run_id=run_id,
                user_id=user_id,
                kind=RunKind.CHAT if conversation_id else RunKind.TRAVEL_PLAN,
                concurrency_key=(
                    concurrency_key(RunKind.CHAT, conversation_id=conversation_id)
                    if conversation_id
                    else concurrency_key(RunKind.TRAVEL_PLAN, run_id=run_id)
                ),
                request_snapshot={"query": "test"},
                conversation_id=conversation_id,
            )

    def test_owner_isolation_and_cursor_ordering(self):
        conversation = self.conversations.create("user-a", "测试")
        first = self.conversations.add_message(
            "user-a", conversation["id"], "user", "第一条"
        )
        second = self.conversations.add_message(
            "user-a", conversation["id"], "assistant", "第二条"
        )
        self.assertEqual((first["sequence"], second["sequence"]), (1, 2))
        self.assertEqual(
            [item["content"] for item in self.conversations.messages(
                "user-a", conversation["id"], after_sequence=1
            )],
            ["第二条"],
        )
        with self.assertRaises(OwnedResourceNotFound):
            self.conversations.get("user-b", conversation["id"])

    def test_first_message_replaces_placeholder_conversation_title(self):
        conversation = self.conversations.create("user-a", "新的旅行对话")
        self.conversations.add_message(
            "user-a", conversation["id"], "user",
            "更偏美食和古城，那就泉州吧。10月12日到15日",
        )
        updated = self.conversations.get("user-a", conversation["id"])
        self.assertEqual(updated["title"], "更偏美食和古城，那就泉州吧。10月12日到15日")

    def test_user_turn_is_atomic_and_blocks_a_second_run_in_the_same_conversation(self):
        conversation = self.conversations.create("user-a", "串行对话")
        first_message, first_run = self.conversations.add_user_message_and_run(
            "user-a", conversation["id"], "帮我规划南京三日游"
        )
        with self.assertRaises(ConversationRunActive):
            self.conversations.add_user_message_and_run(
                "user-a", conversation["id"], "再开一个上海规划"
            )
        messages = self.conversations.messages("user-a", conversation["id"])
        self.assertEqual([item["id"] for item in messages], [first_message["id"]])
        self.assertEqual(self.runs.list("user-a", conversation_id=conversation["id"])[0]["id"], first_run["id"])

    def test_interrupt_answer_is_persisted_once_and_resumes_the_same_run(self):
        conversation = self.conversations.create("user-a", "补充信息")
        _message, run = self.conversations.add_user_message_and_run(
            "user-a", conversation["id"], "帮我规划南京旅行"
        )
        self.runs.transition(run["id"], RunStatus.RUNNING)
        waiting = self.runs.transition(
            run["id"], RunStatus.WAITING_USER,
            outstanding_interaction_id="interaction-1",
        )
        resumed, accepted = self.runs.accept_interaction(
            "user-a", waiting["id"], "interaction-1", "明天出发"
        )
        duplicate, accepted_again = self.runs.accept_interaction(
            "user-a", waiting["id"], "interaction-1", "明天出发"
        )
        self.assertTrue(accepted)
        self.assertFalse(accepted_again)
        self.assertEqual(resumed["id"], run["id"])
        self.assertEqual(duplicate["id"], run["id"])
        self.assertEqual(
            [item["content"] for item in self.conversations.messages("user-a", conversation["id"])],
            ["帮我规划南京旅行", "明天出发"],
        )

    def test_conversation_attention_aggregates_formal_runs_and_view_cursor(self):
        conversation = self.conversations.create("user-a", "后台规划")
        brief = self.briefs.upsert_active(
            "user-a",
            conversation["id"],
            {
                "destination": "南京",
                "start_date": "2026-08-08",
                "end_date": "2026-08-10",
                "trip_focus": "sights_first",
            },
        )
        self.assertEqual(brief["status"], "ready")

        with get_conn(self.db_path) as conn:
            formal = self.runs.insert(
                conn,
                run_id="attention-formal",
                user_id="user-a",
                kind=RunKind.TRAVEL_PLAN,
                concurrency_key="plan:attention-formal",
                request_snapshot={},
                conversation_id=conversation["id"],
            )
            self.runs.insert(
                conn,
                run_id="attention-chat",
                user_id="user-a",
                kind=RunKind.CHAT,
                concurrency_key=f"chat:{conversation['id']}",
                request_snapshot={},
                conversation_id=conversation["id"],
            )

        attention = self.conversations.get("user-a", conversation["id"])
        self.assertTrue(attention["has_active_planning"])
        self.assertTrue(attention["has_ready_brief"])
        self.assertFalse(attention["has_waiting_user"])
        self.assertFalse(attention["has_unread_completed"])

        self.runs.transition(formal["id"], RunStatus.RUNNING)
        self.runs.transition(formal["id"], RunStatus.WAITING_USER)
        attention = self.conversations.get("user-a", conversation["id"])
        self.assertTrue(attention["has_active_planning"])
        self.assertTrue(attention["has_waiting_user"])

        self.runs.transition(formal["id"], RunStatus.RUNNING)
        self.runs.transition(formal["id"], RunStatus.SUCCEEDED)
        attention = self.conversations.get("user-a", conversation["id"])
        self.assertTrue(attention["has_unread_completed"])

        viewed = self.conversations.mark_viewed("user-a", conversation["id"])
        self.assertFalse(viewed["has_unread_completed"])
        with self.assertRaises(OwnedResourceNotFound):
            self.conversations.mark_viewed("user-b", conversation["id"])

    def test_chat_run_does_not_create_planning_attention(self):
        conversation = self.conversations.create("user-a", "普通聊天")
        self.create_run("user-a", conversation["id"])
        attention = self.conversations.list("user-a")[0]
        self.assertTrue(attention["has_active_planning"])
        self.assertFalse(attention["has_unread_completed"])

    def test_conversation_list_can_filter_by_related_itinerary(self):
        with get_conn(self.db_path) as conn:
            related_plan_id = save_itinerary(
                "user-a", {"destination": "南京"}, "南京行程", conn
            )
        message_match = self.conversations.create("user-a", "消息关联")
        run_match = self.conversations.create("user-a", "运行结果关联")
        unrelated = self.conversations.create("user-a", "无关对话")
        other_user = self.conversations.create("user-b", "其他用户")
        self.conversations.add_message(
            "user-a", message_match["id"], "user", "调整行程",
            related_itinerary_id=related_plan_id,
        )
        self.conversations.add_message(
            "user-b", other_user["id"], "user", "调整别人的行程",
            related_itinerary_id=related_plan_id,
        )
        with get_conn(self.db_path) as conn:
            run = self.runs.insert(
                conn,
                run_id="related-result-run",
                user_id="user-a",
                kind=RunKind.TRAVEL_PLAN,
                concurrency_key="plan:related-result-run",
                request_snapshot={},
                conversation_id=run_match["id"],
            )
        self.runs.transition(run["id"], RunStatus.RUNNING)
        self.runs.transition(
            run["id"], RunStatus.SUCCEEDED,
            result_itinerary_id=related_plan_id,
        )

        listed = self.conversations.list(
            "user-a", related_itinerary_id=related_plan_id
        )
        self.assertEqual(
            {item["id"] for item in listed},
            {message_match["id"], run_match["id"]},
        )
        self.assertNotIn(unrelated["id"], {item["id"] for item in listed})

    def test_active_brief_is_reused_and_submission_snapshot_is_immutable(self):
        conversation = self.conversations.create("user-a")
        first = self.briefs.upsert_active(
            "user-a", conversation["id"], {"destination": "云南"}
        )
        second = self.briefs.upsert_active(
            "user-a", conversation["id"], {"days": 5}
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["status"], "collecting")
        self.assertEqual(
            second["missing_fields"],
            ["start_date", "end_date", "trip_focus"],
        )
        second = self.briefs.upsert_active(
            "user-a",
            conversation["id"],
            {"start_date": "2026-10-01", "end_date": "2026-10-05", "trip_focus": "balanced"},
        )
        self.assertEqual(second["status"], "ready")

        def create_run(conn, snapshot, conversation_id):
            return self.runs.insert(
                conn,
                run_id="brief-run",
                user_id="user-a",
                kind=RunKind.TRAVEL_PLAN,
                concurrency_key="plan:brief-run",
                request_snapshot=snapshot,
                conversation_id=conversation_id,
            )

        submitted, run = self.briefs.submit("user-a", second["id"], create_run)
        submitted_again, same_run = self.briefs.submit(
            "user-a", second["id"], create_run
        )
        self.assertEqual(run["id"], same_run["id"])
        self.assertEqual(submitted["submission_snapshot"]["destination"], "云南")
        self.assertEqual(submitted["submission_snapshot"]["days"], 5)
        self.assertEqual(submitted["submission_snapshot"]["trip_focus"], "balanced")
        self.assertEqual(
            submitted_again["submission_snapshot"],
            submitted["submission_snapshot"],
        )

    def test_brief_rejects_invalid_or_reversed_date_ranges(self):
        conversation = self.conversations.create("user-a")
        invalid = self.briefs.upsert_active(
            "user-a",
            conversation["id"],
            {
                "destination": "南京",
                "start_date": "不是日期",
                "end_date": "2026-08-03",
                "trip_focus": "sights_first",
            },
        )
        self.assertEqual(invalid["status"], "collecting")
        self.assertIn("start_date", invalid["missing_fields"])

        reversed_range = self.briefs.upsert_active(
            "user-a",
            conversation["id"],
            {
                "start_date": "2026-08-05",
                "end_date": "2026-08-03",
                "trip_focus": "sights_first",
            },
        )
        self.assertEqual(reversed_range["status"], "collecting")
        self.assertEqual(reversed_range["missing_fields"], ["date_range"])

    def test_run_transitions_and_failed_transaction_rollback(self):
        run = self.create_run()
        running = self.runs.transition(run["id"], RunStatus.RUNNING)
        self.assertEqual(running["status"], "running")
        with self.assertRaises(ValueError):
            self.runs.transition(run["id"], RunStatus.QUEUED)
        self.assertEqual(self.runs.get_internal(run["id"])["status"], "running")

    def test_concurrent_event_sequences_are_monotonic(self):
        run = self.create_run()

        def append(index):
            return self.events.append(run["id"], "custom", {"index": index})

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(append, range(30)))
        events = self.events.after(run["id"])
        self.assertEqual([event["sequence"] for event in events], list(range(1, 31)))
        self.assertEqual(len({event["payload"]["index"] for event in events}), 30)

    def test_wal_mode_and_indexes(self):
        with get_conn(self.db_path) as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        self.assertIn("idx_runs_queue", indexes)
        self.assertIn("idx_run_events_replay", indexes)
