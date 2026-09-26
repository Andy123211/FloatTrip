"""Application service for persistent LLM-understood travel conversations."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.chat.models import DialogueDecision
from app.chat.models import MainAgentContext
from app.chat.artifacts import validate_message_artifacts
from app.chat.prompts import main_agent_messages
from app.core.database import get_conn
from app.chat.memory_service import ChatMemoryService
from app.chat.planning_memory import PlanningMemoryMatcher
from app.core.planning_constraints import compatibility_preferences, planning_instruction
from app.core.planning_brief import required_brief_fields
from app.core.travel_memory import ConversationMemoryRepository
from app.runtime.manager import RunManager
from app.runtime.models import RunKind, concurrency_key
from app.runtime.repositories import (
    ConversationRunActive,
    ConversationRepository,
    OwnedResourceNotFound,
    PlanningBriefRepository,
    RunRepository,
)
from app.runtime.observability import metrics


class ChatService:
    def __init__(self, manager: RunManager, db_path: str | Path | None = None):
        self.manager = manager
        self.conversations = ConversationRepository(db_path)
        self.briefs = PlanningBriefRepository(db_path)
        self.runs = RunRepository(db_path)
        self.memory_context = ChatMemoryService(db_path)
        self.planning_memory = PlanningMemoryMatcher(db_path)
        # Local import avoids a service/executor import cycle during module loading.
        from app.chat.executor import DialogueActionExecutor

        self.actions = DialogueActionExecutor(self)

    async def submit_message(
        self,
        user_id: str,
        conversation_id: str,
        content: str,
        *,
        related_run_id: str | None = None,
        related_itinerary_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Persist a message and queue understanding; never infer text semantics here."""
        self.memory_context.validate_message(content)
        if related_run_id:
            await asyncio.to_thread(self.runs.get, user_id, related_run_id)
        if related_itinerary_id:
            await asyncio.to_thread(
                self._owned_itinerary_summary, user_id, related_itinerary_id
            )
        message, run = await asyncio.to_thread(
            self.conversations.add_user_message_and_run,
            user_id,
            conversation_id,
            content,
            related_run_id=related_run_id,
            related_itinerary_id=related_itinerary_id,
        )
        return message, run

    async def submit_brief(
        self, user_id: str, brief_id: str, *, origin_chat_run_id: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current = await asyncio.to_thread(self.briefs.get, user_id, brief_id)
        missing = required_brief_fields(current.get("data") or {})
        if missing:
            raise ValueError("planning brief is not ready: " + ",".join(missing))
        if current["status"] in self.briefs.ACTIVE:
            current = await self.planning_memory.refresh(user_id, brief_id)

        def create_run(conn, snapshot, conversation_id):
            active = conn.execute(
                "SELECT * FROM runs WHERE conversation_id=? AND user_id=? "
                "AND kind IN ('chat','travel_plan','revision') "
                "AND status IN ('queued','running','waiting_user') "
                "AND (? IS NULL OR id<>?) ORDER BY created_at DESC,id DESC LIMIT 1",
                (conversation_id, user_id, origin_chat_run_id, origin_chat_run_id),
            ).fetchone()
            if active:
                raise ConversationRunActive(dict(active))
            run_id = str(uuid.uuid4())
            request = dict(snapshot)
            if origin_chat_run_id:
                request["origin_chat_run_id"] = origin_chat_run_id
            request.update(compatibility_preferences(request.get("effective_constraints") or []))
            request.setdefault("query", self._snapshot_query(request))
            request["planning_instruction"] = planning_instruction(request)
            memory = ConversationMemoryRepository(self.manager.db_path).ensure_snapshot(
                user_id, conversation_id, conn
            )
            request["memory_profile_revision"] = memory["profile_revision"]
            request["memory_profile_snapshot"] = memory["profile_snapshot"]
            return self.runs.insert(
                conn,
                run_id=run_id,
                user_id=user_id,
                kind=RunKind.TRAVEL_PLAN,
                concurrency_key=concurrency_key(RunKind.TRAVEL_PLAN, run_id=run_id),
                request_snapshot=request,
                conversation_id=conversation_id,
            )

        return await asyncio.to_thread(
            self.briefs.submit, user_id, brief_id, create_run
        )

    async def execute_planner_tool(
        self, run: dict[str, Any], brief_id: str
    ) -> tuple[dict[str, Any], str]:
        """Run Planner as an eventful task tool inside the top-level Chat Run."""
        current = await asyncio.to_thread(self.briefs.get, run["user_id"], brief_id)
        missing = required_brief_fields(current.get("data") or {})
        if missing:
            raise ValueError("planning brief is not ready: " + ",".join(missing))
        if current["status"] in self.briefs.ACTIVE:
            current = await self.planning_memory.refresh(run["user_id"], brief_id)
            submitted, _same_run = await asyncio.to_thread(
                self.briefs.freeze_for_run, run["user_id"], brief_id, run["id"]
            )
        elif current["status"] == "submitted":
            # A retried Chat Run reuses the immutable submission snapshot. The
            # Planner remains an internal task and must not create another Run.
            submitted = current
        else:
            raise ValueError("planning brief is not ready")
        await self.manager.publish(
            run["id"], "custom", {
                "kind": "planning_brief.submitted",
                "brief_id": submitted["id"],
                "status": "submitted",
                "summary": submitted["data"],
                "missing_fields": [],
            }, durable=True,
        )
        request = dict(submitted.get("submission_snapshot") or submitted["data"])
        request.update(compatibility_preferences(request.get("effective_constraints") or []))
        request.setdefault("query", self._snapshot_query(request))
        request["planning_instruction"] = planning_instruction(request)
        memory = await asyncio.to_thread(
            self.memory_context.memories.get, run["user_id"], run["conversation_id"]
        )
        request["memory_profile_revision"] = memory["profile_revision"]
        request["memory_profile_snapshot"] = memory["profile_snapshot"]
        task_run = {**run, "request_snapshot": request}

        from app.planning.graph import build_graph
        from app.planning.runtime_worker import PlanningFinalizer, planning_run_to_state

        graph = build_graph(memory_writer=None, interrupt_on_missing=False)
        state = await planning_run_to_state(task_run)
        latest: dict[str, Any] = {}
        async for part in graph.astream(
            state, stream_mode=["custom", "values"], version="v2",
            config={
                "configurable": {
                    "thread_id": str(run["conversation_id"]),
                    "checkpoint_ns": f"run:{run['id']}:planner",
                }
            },
        ):
            if part.get("type") == "custom" and isinstance(part.get("data"), dict):
                await self.manager.publish(run["id"], "custom", part["data"], durable=True)
            elif part.get("type") == "values":
                values = self._graph_values(part.get("data"))
                if values:
                    latest = {
                        key: value for key, value in values.items()
                        if not key.startswith("__")
                    }
        result = await PlanningFinalizer(self.manager, tips_as_run=False)(task_run, latest, "") or {}
        itinerary_id = str(result.get("result_itinerary_id") or "")
        if not itinerary_id:
            raise RuntimeError("planner task completed without itinerary")
        return submitted, itinerary_id

    async def execute_revision_tool(
        self, run: dict[str, Any], itinerary_id: str, modification_notes: str
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Run Revision as a task tool; bubble any subagent question to the caller."""
        bound = run["request_snapshot"].get("related_itinerary_id")
        if bound and bound != itinerary_id:
            from app.planning.revision import RevisionSearchFailure
            error = RevisionSearchFailure("revision target mismatch")
            error.public_code = "revision_target_mismatch"
            error.public_message = "这条消息引用的是另一份行程，请重新选择要修改的方案。"
            error.public_retryable = False
            raise error
        summary = await asyncio.to_thread(
            self._owned_itinerary_summary, run["user_id"], itinerary_id
        )
        # Accepted answers are also available to pre-upgrade waiting tools whose
        # old checkpoint had no revision child graph. Never silently submit one.
        def accepted_answers():
            with get_conn(self.manager.db_path) as conn:
                rows = conn.execute(
                    "SELECT value_json FROM run_interaction_responses WHERE run_id=? ORDER BY created_at,interaction_id",
                    (run["id"],),
                ).fetchall()
            return [json.loads(row["value_json"]) for row in rows]
        user_message = str(run["request_snapshot"].get("text") or modification_notes)
        for answer in await asyncio.to_thread(accepted_answers):
            text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
            if text.strip():
                if text.strip() not in modification_notes:
                    modification_notes += f"\n【用户补充】{text.strip()}"
                if text.strip() not in user_message:
                    user_message += f"\n【用户补充】{text.strip()}"
        memory = await asyncio.to_thread(
            self.memory_context.memories.get, run["user_id"], run["conversation_id"]
        )
        task_run = {
            **run,
            "request_snapshot": {
                "modification_notes": modification_notes,
                "revision_user_message": user_message,
                "parent_plan_id": itinerary_id,
                "related_itinerary_id": itinerary_id,
                "destination": summary.get("destination"),
                "memory_profile_revision": memory["profile_revision"],
                "memory_profile_snapshot": memory["profile_snapshot"],
            },
        }
        from app.planning.graph import build_runtime_revision_graph
        from app.planning.runtime_worker import (
            PlanningFinalizer, revision_snapshot_to_state,
        )

        # Inherit the parent graph checkpoint and task namespace. LangGraph resumes
        # the interrupted child node, so completed search nodes are not replayed.
        graph = build_runtime_revision_graph(checkpointer=True)
        state = await revision_snapshot_to_state(task_run, self.manager.db_path)
        latest: dict[str, Any] = {}
        async for part in graph.astream(
            state, stream_mode=["custom", "updates", "values"], version="v2", durability="sync",
        ):
            data = part.get("data") or {}
            if part.get("type") == "custom" and isinstance(data, dict):
                await self.manager.publish(run["id"], "custom", data, durable=True)
            elif part.get("type") in {"updates", "values"}:
                interrupts = (
                    data.get("__interrupt__") if isinstance(data, dict) else None
                ) or part.get("interrupts") or ()
                if interrupts:
                    from langgraph.errors import GraphInterrupt
                    raise GraphInterrupt(tuple(interrupts))
                if part.get("type") == "values":
                    values = self._graph_values(data)
                    if values:
                        latest = {
                            key: value for key, value in values.items()
                            if not key.startswith("__")
                        }
        result = await PlanningFinalizer(self.manager, tips_as_run=False)(task_run, latest, "") or {}
        result_id = str(result.get("result_itinerary_id") or "")
        return (result_id or None), None

    async def apply_brief_patch(
        self,
        run: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        brief = await asyncio.to_thread(
            self.briefs.upsert_active,
            run["user_id"],
            run["conversation_id"],
            patch,
        )
        brief = await self.planning_memory.refresh(run["user_id"], brief["id"])
        event_kind = (
            "planning_brief.ready"
            if brief["status"] == "ready"
            else "planning_brief.updated"
        )
        await self.manager.publish(
            run["id"],
            "custom",
            {
                "kind": event_kind,
                "brief_id": brief["id"],
                "status": brief["status"],
                "summary": brief["data"],
                "missing_fields": brief["missing_fields"],
                "memory_context": brief["memory_context"],
                "effective_constraints": brief["effective_constraints"],
                "constraint_coverage": brief["constraint_coverage"],
            },
            durable=True,
        )
        return brief

    async def update_brief(
        self, user_id: str, brief_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        current = await asyncio.to_thread(self.briefs.get, user_id, brief_id)
        brief = await asyncio.to_thread(
            self.briefs.upsert_active,
            user_id,
            current["conversation_id"],
            patch,
        )
        return await self.planning_memory.refresh(user_id, brief["id"])

    async def refresh_brief_memory(
        self, user_id: str, brief_id: str
    ) -> dict[str, Any]:
        return await self.planning_memory.refresh(user_id, brief_id)

    async def publish_assistant_message(
        self,
        run: dict[str, Any],
        content: str,
        *,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        message = await asyncio.to_thread(
            self.conversations.add_message,
            run["user_id"],
            run["conversation_id"],
            "assistant",
            content,
            related_run_id=run["id"],
            artifacts=artifacts,
        )
        await self.manager.publish(
            run["id"],
            "custom",
            {
                "kind": "chat.message.completed",
                "message_id": message["id"],
                "content": message["content"],
                "sequence": message["sequence"],
                "created_at": message["created_at"],
                "artifacts": message["artifacts"],
            },
            durable=True,
            validate_custom=False,
        )
        return message

    async def finalize_chat(
        self,
        run: dict[str, Any],
        updates: dict[str, Any],
        _assistant_text: str,
    ) -> dict[str, Any] | None:
        if updates.get("planning_handoff"):
            pending = dict(updates.get("pending_brief") or {})
            brief, error = await self.actions.apply_brief_patch(run, pending)
            if error or not brief:
                return await self.publish_assistant_message(
                    run, error or "旅行需求暂时无法保存，请重新补充。"
                )
            submitted, itinerary_id = await self.execute_planner_tool(run, brief["id"])
            return {
                "brief_id": brief["id"],
                "result_itinerary_id": itinerary_id,
                "planning_handoff": True,
            }
        decision = DialogueDecision.model_validate(updates.get("decision") or {})
        return await self.actions.execute(run, decision)

    @staticmethod
    def _graph_values(value: Any) -> dict[str, Any]:
        """Normalize LangGraph values for dict and Pydantic state schemas."""
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            return dumped if isinstance(dumped, dict) else {}
        return {}

    async def chat_input(self, run: dict[str, Any]) -> dict[str, Any]:
        """Build the bounded, owner-scoped facts available to the dialogue LLM.

        The current message is deliberately kept separate from history.  This
        prevents a retried Chat Run from presenting the same user turn twice
        and keeps the agent's short-term memory stable as a conversation grows.
        """
        active = await asyncio.to_thread(
            self.briefs.active_for_conversation,
            run["user_id"],
            run["conversation_id"],
        )
        targets = await asyncio.to_thread(
            self._conversation_targets,
            run["user_id"],
            run["conversation_id"],
        )
        snapshot = run["request_snapshot"]
        itinerary_id = snapshot.get("related_itinerary_id")
        itinerary = (
            await asyncio.to_thread(
                self._owned_itinerary_summary, run["user_id"], itinerary_id
            )
            if itinerary_id
            else None
        )
        application_state = {
            "today": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "timezone": "Asia/Shanghai",
            "planning_brief": self._brief_context(active),
            "available_targets": targets,
            "explicit_target": {
                "run_id": snapshot.get("related_run_id"),
                "itinerary_id": itinerary_id,
                "itinerary": itinerary,
            },
        }
        context = await self.memory_context.prepare(
            run, application_state=application_state
        )
        return {"dialogue_context": context}

    async def react_chat_input(self, run: dict[str, Any]) -> dict[str, Any]:
        """Rebuild one ReAct turn from the database without dynamic app state."""
        context = await self.memory_context.prepare(
            run,
            application_state={
                "today": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                "timezone": "Asia/Shanghai",
            },
        )
        return {"messages": main_agent_messages(context)}

    async def main_agent_context(self, run: dict[str, Any]) -> MainAgentContext:
        memory = await asyncio.to_thread(
            self.memory_context.memories.get,
            run["user_id"],
            run["conversation_id"],
        )
        snapshot = run["request_snapshot"]
        return MainAgentContext(
            user_id=run["user_id"],
            conversation_id=run["conversation_id"],
            chat_run_id=run["id"],
            memory_revision=int(memory["profile_revision"]),
            current_message=str(snapshot.get("text") or ""),
            related_run_id=snapshot.get("related_run_id"),
            related_itinerary_id=snapshot.get("related_itinerary_id"),
        )

    async def finalize_react_chat(
        self,
        run: dict[str, Any],
        updates: dict[str, Any],
        assistant_text: str,
    ) -> dict[str, Any]:
        """Persist the final model answer together with artifacts from tool messages."""
        messages = list(updates.get("messages") or [])
        current_start = max(
            (index for index, message in enumerate(messages)
             if getattr(message, "type", None) == "human"),
            default=-1,
        ) + 1
        artifacts: list[dict[str, Any]] = []
        final_text = assistant_text
        agent_turns = 0
        tool_calls = 0
        cache_read = 0
        cache_miss = 0
        planning_handoff = False
        handoff_itinerary_id = ""
        for message in messages[current_start:]:
            message_type = getattr(message, "type", None)
            if message_type == "tool":
                tool_calls += 1
                try:
                    tool_payload = json.loads(str(getattr(message, "content", "") or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    tool_payload = {}
                if (
                    tool_payload.get("ok") is True
                    and isinstance(tool_payload.get("data"), dict)
                    and tool_payload["data"].get("itinerary_id")
                ):
                    planning_handoff = True
                    handoff_itinerary_id = str(tool_payload["data"]["itinerary_id"])
                artifact = getattr(message, "artifact", None)
                if isinstance(artifact, dict):
                    artifacts.append(artifact)
            elif message_type == "ai":
                agent_turns += 1
                content = self._message_text(message)
                if content and not getattr(message, "tool_calls", None):
                    final_text = content
                usage = getattr(message, "usage_metadata", None) or {}
                details = usage.get("input_token_details") or {}
                message_cache_read = int(details.get("cache_read") or details.get("cache_read_tokens") or 0)
                input_tokens = int(usage.get("input_tokens") or 0)
                cache_read += message_cache_read
                cache_miss += int(
                    details.get("cache_miss") or details.get("cache_creation")
                    or max(0, input_tokens - message_cache_read)
                )
        unique_artifacts: list[dict[str, Any]] = []
        artifact_keys: set[str] = set()
        for artifact in artifacts:
            key = str(artifact)
            if key not in artifact_keys:
                artifact_keys.add(key)
                unique_artifacts.append(artifact)
        safe_artifacts = validate_message_artifacts(unique_artifacts[:5])
        if not str(final_text or "").strip():
            final_text = "我已经处理了这条旅行消息。"
        metrics.increment("main_agent_turns", agent_turns)
        metrics.increment("main_agent_tool_calls", tool_calls)
        metrics.increment("provider_cache_hit_tokens", cache_read)
        metrics.increment("provider_cache_miss_tokens", cache_miss)
        metrics.log(
            "main_agent_completed", run_id=run["id"], agent_turns=agent_turns,
            tool_calls=tool_calls, cache_hit_tokens=cache_read,
            cache_miss_tokens=cache_miss,
        )
        if planning_handoff:
            messages = await asyncio.to_thread(
                self.conversations.messages,
                run["user_id"], run["conversation_id"], limit=100,
            )
            final_message = next((
                item for item in reversed(messages)
                if item.get("role") == "assistant"
                and item.get("related_run_id") == run["id"]
                and item.get("related_itinerary_id") == handoff_itinerary_id
            ), None)
            if final_message:
                await self.manager.publish(
                    run["id"], "custom", {
                        "kind": "chat.message.completed",
                        "message_id": final_message["id"],
                        "content": final_message["content"],
                        "sequence": final_message["sequence"],
                        "created_at": final_message["created_at"],
                        "artifacts": final_message["artifacts"],
                        "related_itinerary_id": handoff_itinerary_id,
                    }, durable=True, validate_custom=False,
                )
            return {
                "planning_handoff": True,
                "result_itinerary_id": handoff_itinerary_id,
            }
        return await self.publish_assistant_message(
            run, str(final_text).strip(), artifacts=safe_artifacts
        )

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text") or "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return ""

    def _conversation_targets(
        self, user_id: str, conversation_id: str
    ) -> list[dict[str, Any]]:
        runs = self.runs.list(user_id, conversation_id=conversation_id, limit=20)
        targets: list[dict[str, Any]] = []
        for item in runs:
            if item["kind"] not in {RunKind.TRAVEL_PLAN.value, RunKind.REVISION.value}:
                continue
            snapshot = item["request_snapshot"]
            itinerary_id = item.get("result_itinerary_id") or snapshot.get(
                "related_itinerary_id"
            )
            targets.append(
                {
                    "run_id": item["id"],
                    "itinerary_id": itinerary_id,
                    "kind": item["kind"],
                    "status": item["status"],
                    "destination": snapshot.get("destination", ""),
                }
            )
        return targets

    def _owned_itinerary_summary(
        self, user_id: str, itinerary_id: str | None
    ) -> dict[str, Any]:
        if not itinerary_id:
            raise OwnedResourceNotFound("base itinerary not found")
        with get_conn(self.manager.db_path) as conn:
            row = conn.execute(
                "SELECT id,destination,start_date,end_date,version,query "
                "FROM itineraries WHERE id=? AND user_id=?",
                (itinerary_id, user_id),
            ).fetchone()
        if not row:
            raise OwnedResourceNotFound("base itinerary not found")
        return dict(row)

    def _itinerary_is_modifiable(self, user_id: str, itinerary_id: str) -> bool:
        """Check the private checkpoint without ever placing it in LLM context."""
        with get_conn(self.manager.db_path) as conn:
            row = conn.execute(
                "SELECT planner_state_json FROM itineraries WHERE id=? AND user_id=?",
                (itinerary_id, user_id),
            ).fetchone()
        return bool(row and row["planner_state_json"])

    @staticmethod
    def _brief_context(brief: dict[str, Any] | None) -> dict[str, Any] | None:
        if not brief:
            return None
        return {
            "id": brief["id"],
            "status": brief["status"],
            "data": brief["data"],
            "missing_fields": brief["missing_fields"],
            "memory_context": brief.get("memory_context"),
            "effective_constraints": brief.get("effective_constraints") or [],
        }

    @staticmethod
    def _snapshot_query(snapshot: dict[str, Any]) -> str:
        parts = [str(snapshot.get("destination") or "").strip()]
        if snapshot.get("start_date") and snapshot.get("end_date"):
            parts.append(f"{snapshot['start_date']}至{snapshot['end_date']}")
        elif snapshot.get("days"):
            parts.append(f"{snapshot['days']}日游")
        if snapshot.get("trip_budget"):
            parts.append(f"本次预算：{snapshot['trip_budget']}")
        for item in snapshot.get("effective_constraints") or []:
            if item.get("value_text"):
                parts.append(str(item["value_text"]))
        return "，".join(part for part in parts if part)
