"""Deterministic execution of validated dialogue decisions.

This module intentionally never receives or reads the raw user message.  The
LLM's validated structured decision is the boundary between language
understanding and business-state changes.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from app.chat.models import DialogueDecision, PlanningBriefPatch
from app.core.planning_brief import required_brief_fields
from app.runtime.models import RunStatus
from app.runtime.repositories import OwnedResourceNotFound

if TYPE_CHECKING:
    from app.chat.service import ChatService


class DialogueActionExecutor:
    def __init__(self, service: "ChatService"):
        self.service = service

    async def execute(
        self, run: dict[str, Any], decision: DialogueDecision
    ) -> dict[str, Any]:
        """Apply a decision with no text inference or model-controlled state bypass."""
        reply = decision.clarification.question if decision.clarification else decision.reply
        result: dict[str, Any] = {}
        task_completed = False

        if decision.intent in {"create_plan", "update_brief"}:
            brief, error = await self._apply_brief(run, decision)
            if error:
                reply = error
            elif brief:
                result["brief_id"] = brief["id"]
        elif decision.intent == "confirm_plan":
            brief = await asyncio.to_thread(
                self.service.briefs.active_for_conversation,
                run["user_id"], run["conversation_id"],
            )
            if not brief:
                reply = "目前没有可确认的旅行需求。"
            else:
                missing = required_brief_fields(brief["data"])
                if missing:
                    reply = "旅行需求还不完整，请继续补充。"
                else:
                    submitted, itinerary_id = await self.service.execute_planner_tool(
                        run, brief["id"]
                    )
                    result.update(
                        brief_id=submitted["id"], result_itinerary_id=itinerary_id,
                    )
                    task_completed = True
        elif decision.intent == "modify_itinerary":
            itinerary_id, error = await self._resolve_itinerary(run, decision)
            if error:
                reply = error
            elif not decision.modification_notes:
                reply = "我还不清楚要如何调整这份行程，请补充具体改动。"
            else:
                result_id, concern = await self.service.execute_revision_tool(
                    run, itinerary_id, decision.modification_notes
                )
                if concern:
                    reply = str(concern.get("question") or "请补充这次修改的必要信息。")
                elif result_id:
                    result["result_itinerary_id"] = result_id
                    task_completed = True
        elif decision.intent == "run_control":
            controlled, error = await self._control_run(run, decision)
            if error:
                reply = error
            elif controlled:
                result["controlled_run_id"] = controlled["id"]

        if not task_completed:
            await self.service.publish_assistant_message(run, reply)
        return result

    async def apply_brief_patch(
        self, run: dict[str, Any], patch: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Shared deterministic entry point for the ReAct tool path."""
        try:
            parsed = PlanningBriefPatch.model_validate(patch)
        except Exception:
            return None, "旅行需求字段不符合要求，请检查日期、天数和约束。"
        decision = DialogueDecision(
            intent="update_brief", reply="已更新旅行需求。", brief_patch=parsed
        )
        return await self._apply_brief(run, decision)

    async def control_run(
        self, run: dict[str, Any], run_id: str, action: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        if action not in {"cancel", "retry"}:
            return None, "只支持停止或重试任务。"
        decision = DialogueDecision(
            intent="run_control",
            reply="任务状态已更新。",
            target={"run_id": run_id},
            run_action=action,
        )
        return await self._control_run(run, decision)

    async def _apply_brief(
        self, run: dict[str, Any], decision: DialogueDecision
    ) -> tuple[dict[str, Any] | None, str | None]:
        patch = decision.brief_patch.model_dump(exclude_none=True, exclude_unset=True)
        if not patch:
            return None, "我还需要一点旅行信息，才能继续整理这趟行程。"
        if not self._patch_dates_valid(patch):
            return None, "日期范围看起来不正确，请补充有效的开始和结束日期。"
        active = await asyncio.to_thread(
            self.service.briefs.active_for_conversation,
            run["user_id"],
            run["conversation_id"],
        )
        combined = {
            **((active or {}).get("data") or {}),
            **patch,
        }
        existing_constraints = list(((active or {}).get("data") or {}).get("trip_constraints") or [])
        remove_ids = {str(value) for value in (patch.pop("remove_trip_constraint_ids", []) or [])}
        constraints_by_id = {
            str(item.get("id")): dict(item)
            for item in existing_constraints
            if item.get("id") and str(item.get("id")) not in remove_ids
        }
        for item in (patch.pop("trip_constraints", []) or []):
            item_id = str(item.get("id") or "")
            if item_id and item_id in constraints_by_id:
                constraints_by_id[item_id] = item
            else:
                key = (item.get("category"), str(item.get("value_text") or "").casefold(), item.get("polarity"))
                duplicate = next((current_id for current_id, current in constraints_by_id.items() if (
                    current.get("category"), str(current.get("value_text") or "").casefold(), current.get("polarity")
                ) == key), None)
                constraints_by_id[duplicate or item_id or f"new:{len(constraints_by_id)}"] = item
        excluded = set(((active or {}).get("data") or {}).get("excluded_memory_fact_ids") or [])
        excluded.update(patch.pop("excluded_memory_fact_ids", []) or [])
        excluded.difference_update(patch.pop("restored_memory_fact_ids", []) or [])
        if constraints_by_id or existing_constraints:
            combined["trip_constraints"] = list(constraints_by_id.values())
            patch["trip_constraints"] = combined["trip_constraints"]
        if excluded:
            combined["excluded_memory_fact_ids"] = sorted(excluded)
            patch["excluded_memory_fact_ids"] = sorted(excluded)
        if not self._combined_dates_valid(combined):
            return None, "结束日期不能早于开始日期，请确认日期范围。"
        self._normalize_days(combined)
        # Action-only fields never enter durable brief data.  Supplying the
        # complete canonical brief also makes list replacement deterministic.
        durable_fields = {
            "destination", "start_date", "end_date", "days", "trip_focus", "budget",
            "trip_budget", "attraction_preference", "food_preference",
            "habit_preference", "trip_constraints", "excluded_memory_fact_ids",
        }
        patch = {key: value for key, value in combined.items() if key in durable_fields}
        brief = await self.service.apply_brief_patch(run, patch)
        return brief, None

    async def _control_run(
        self, run: dict[str, Any], decision: DialogueDecision
    ) -> tuple[dict[str, Any] | None, str | None]:
        target_id, error = await self._resolve_run(run, decision)
        if error:
            return None, error
        if decision.requires_confirmation:
            return None, "请在对应任务卡片上确认这项操作，避免误影响正在进行的规划。"
        try:
            target = await asyncio.to_thread(
                self.service.runs.get, run["user_id"], target_id
            )
        except OwnedResourceNotFound:
            return None, "这个任务不可用，请重新选择。"
        if decision.run_action == "cancel":
            if target["status"] in {
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
            }:
                return None, "这个任务已经结束，不能再停止。"
            return await self.service.manager.cancel(run["user_id"], target_id), None
        if decision.run_action == "retry":
            if target["status"] not in {
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
            }:
                return None, "只有已停止或未完成的任务可以重新尝试。"
            try:
                retried = await asyncio.to_thread(
                    self.service.manager.retry, run["user_id"], target_id
                )
            except ValueError:
                # The run may have changed while this Chat Run was waiting in
                # the queue.  Do not expose a race or turn it into a failure.
                return None, "这个任务当前不能重新尝试，请刷新后再查看状态。"
            return retried, None
        return None, "请说明是要停止还是重新尝试这项任务。"

    async def _resolve_itinerary(
        self, run: dict[str, Any], decision: DialogueDecision
    ) -> tuple[str | None, str | None]:
        explicit = run["request_snapshot"].get("related_itinerary_id")
        if explicit:
            if decision.target.itinerary_id and decision.target.itinerary_id != explicit:
                return None, "这条消息已绑定另一份行程，请在对应行程中继续修改。"
            return explicit, None
        targets = await asyncio.to_thread(
            self.service._conversation_targets,
            run["user_id"],
            run["conversation_id"],
        )
        candidates = {
            item["itinerary_id"]
            for item in targets
            if item.get("itinerary_id")
        }
        requested = decision.target.itinerary_id
        if requested and requested in candidates:
            return requested, None
        if not requested and len(candidates) == 1:
            return next(iter(candidates)), None
        return None, "我找到了多个可能的行程，请先选择要修改的那一份。"

    async def _resolve_run(
        self, run: dict[str, Any], decision: DialogueDecision
    ) -> tuple[str | None, str | None]:
        explicit = run["request_snapshot"].get("related_run_id")
        if explicit:
            if decision.target.run_id and decision.target.run_id != explicit:
                return None, "这条消息已绑定另一项任务，请在对应任务中操作。"
            return explicit, None
        targets = await asyncio.to_thread(
            self.service._conversation_targets,
            run["user_id"],
            run["conversation_id"],
        )
        candidates = {
            item["run_id"]
            for item in targets
            if item["status"]
            in {RunStatus.QUEUED.value, RunStatus.RUNNING.value, RunStatus.WAITING_USER.value,
                RunStatus.FAILED.value, RunStatus.CANCELLED.value}
        }
        requested = decision.target.run_id
        if requested and requested in candidates:
            return requested, None
        if not requested and len(candidates) == 1:
            return next(iter(candidates)), None
        return None, "我找到了多个可能的任务，请先选择具体任务。"

    @staticmethod
    def _parse_date(value: object) -> date | None:
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    def _patch_dates_valid(self, patch: dict[str, Any]) -> bool:
        for key in ("start_date", "end_date"):
            if key in patch and self._parse_date(patch[key]) is None:
                return False
        return True

    def _combined_dates_valid(self, data: dict[str, Any]) -> bool:
        start = self._parse_date(data.get("start_date"))
        end = self._parse_date(data.get("end_date"))
        return not (start and end and end < start)

    def _normalize_days(self, data: dict[str, Any]) -> None:
        start = self._parse_date(data.get("start_date"))
        end = self._parse_date(data.get("end_date"))
        days = data.get("days")
        # A start date plus a duration is a complete calendar range.  This is
        # deliberately deterministic so a missed optional tool argument from
        # the dialogue model cannot leave an otherwise unambiguous Brief in
        # the "collecting" state.
        if start and end is None and isinstance(days, int) and days >= 1:
            data["end_date"] = (start + timedelta(days=days - 1)).isoformat()
            return
        if start and end:
            data["days"] = (end - start).days + 1
