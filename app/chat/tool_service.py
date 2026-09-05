"""Owner-scoped deterministic operations exposed to the main Agent."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from langgraph.types import interrupt
from zoneinfo import ZoneInfo

from app.chat.artifacts import ItineraryCard, ItineraryCollectionArtifact
from app.chat.models import AgentToolResult, MainAgentContext
from app.core.planning_brief import required_brief_fields, required_input_interrupt
from app.core.database import get_conn
from app.runtime.models import RunKind
from app.runtime.repositories import OwnedResourceNotFound


MEMORY_CATEGORIES = {
    "attraction_preference", "food_preference", "dietary_requirement",
    "travel_pace", "budget_style", "transport_preference",
    "accommodation_preference", "schedule_preference", "companion_context",
    "accessibility_need", "destination_history", "other_travel_preference",
}
MEMORY_POLARITIES = {"prefer", "avoid", "require", "fact"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _date_days(start: Any, end: Any) -> int | None:
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days + 1
    except (TypeError, ValueError):
        return None


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"], "kind": run["kind"], "status": run["status"],
        "conversation_id": run.get("conversation_id"),
        "result_itinerary_id": run.get("result_itinerary_id"),
        "created_at": run.get("created_at"), "updated_at": run.get("updated_at"),
    }


class MainAgentToolService:
    def __init__(self, chat_service: Any, db_path: str | Path | None = None):
        self.chat = chat_service
        self.db_path = db_path if db_path is not None else chat_service.manager.db_path

    def get_travel_memory(
        self,
        context: MainAgentContext,
        *,
        categories: list[str] | None = None,
        destination: str | None = None,
        companion: str | None = None,
        polarities: list[str] | None = None,
        include_provenance: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        chosen_categories = set(categories or MEMORY_CATEGORIES)
        chosen_polarities = set(polarities or MEMORY_POLARITIES)
        if not chosen_categories <= MEMORY_CATEGORIES:
            raise ValueError("invalid memory category")
        if not chosen_polarities <= MEMORY_POLARITIES:
            raise ValueError("invalid memory polarity")
        state = self.chat.memory_context.memories.get(
            context.user_id, context.conversation_id
        )
        if int(state["profile_revision"]) != context.memory_revision:
            raise ValueError("frozen memory revision mismatch")
        destination_key = _clean_text(destination)
        companion_key = _clean_text(companion)
        facts: list[dict[str, Any]] = []
        for fact in state.get("profile_snapshot") or []:
            if fact.get("status") != "active":
                continue
            if fact.get("category") not in chosen_categories:
                continue
            if fact.get("polarity") not in chosen_polarities:
                continue
            scope = fact.get("scope_key") or {}
            if destination_key and fact.get("scope_type") in {"destination", "destination_companion"}:
                if _clean_text(scope.get("destination")) != destination_key:
                    continue
            if companion_key and fact.get("scope_type") in {"companion", "destination_companion"}:
                if companion_key not in _clean_text(scope.get("companion")):
                    continue
            item = {
                "id": fact.get("id"), "category": fact.get("category"),
                "value_text": fact.get("value_text"), "polarity": fact.get("polarity"),
                "scope_type": fact.get("scope_type"), "scope_key": scope,
            }
            if include_provenance:
                item["provenance"] = {
                    "source_kind": fact.get("source_kind"),
                    "source_conversation_id": fact.get("source_conversation_id"),
                    "evidence_sequences": list(fact.get("evidence_sequences") or [])[:20],
                }
            facts.append(item)
        facts.sort(key=lambda item: (str(item["category"]), str(item["id"])))
        bounded = facts[: max(1, min(int(limit), 50))]
        return {
            "memory_revision": context.memory_revision,
            "facts": bounded,
            "count": len(bounded),
            "has_more": len(facts) > len(bounded),
        }

    def get_planning_context(self, context: MainAgentContext) -> dict[str, Any]:
        brief = self.chat.briefs.active_for_conversation(
            context.user_id, context.conversation_id
        )
        runs = self.chat.runs.list(
            context.user_id, conversation_id=context.conversation_id, limit=20
        )
        explicit_itinerary = None
        if context.related_itinerary_id:
            try:
                explicit_itinerary = self.chat._owned_itinerary_summary(
                    context.user_id, context.related_itinerary_id
                )
            except OwnedResourceNotFound:
                explicit_itinerary = None
        return {
            "today": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "timezone": "Asia/Shanghai",
            "planning_brief": self.chat._brief_context(brief),
            "missing_fields": list((brief or {}).get("missing_fields") or []),
            "active_runs": [
                _public_run(item) for item in runs
                if item["status"] in {"queued", "running", "waiting_user"}
            ],
            "available_targets": self.chat._conversation_targets(
                context.user_id, context.conversation_id
            ),
            "explicit_target": {
                "run_id": context.related_run_id,
                "itinerary_id": context.related_itinerary_id,
                "itinerary": explicit_itinerary,
            },
        }

    def find_saved_itineraries(
        self,
        context: MainAgentContext,
        *,
        destination: str | None = None,
        duration_days: int | None = None,
        travel_start_from: str | None = None,
        travel_start_to: str | None = None,
        include_revisions: bool = False,
        limit: int = 5,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        bounded_limit = max(1, min(int(limit), 5))
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id,parent_id,root_id,version,destination,start_date,end_date,"
                "created_at,modification_notes,plan_json FROM itineraries "
                "WHERE user_id=? ORDER BY created_at DESC,id DESC",
                (context.user_id,),
            ).fetchall()
        decoded = [self._itinerary_row(row) for row in rows]
        if not include_revisions:
            latest: dict[str, dict[str, Any]] = {}
            for item in decoded:
                root_id = item["root_id"]
                previous = latest.get(root_id)
                if previous is None or (item["version"], item["created_at"], item["itinerary_id"]) > (
                    previous["version"], previous["created_at"], previous["itinerary_id"]
                ):
                    latest[root_id] = item
            decoded = list(latest.values())
        decoded.sort(key=lambda item: (item["created_at"], item["itinerary_id"]), reverse=True)
        exact = [item for item in decoded if self._matches_itinerary(
            item, destination=destination, duration_days=duration_days,
            travel_start_from=travel_start_from, travel_start_to=travel_start_to,
        )]
        near = [item for item in decoded if item not in exact and self._near_itinerary(
            item, destination=destination, duration_days=duration_days,
        )]
        chosen = (exact or near)[:bounded_limit]
        match_kind = "exact" if exact else "near"
        artifact = None
        if chosen:
            artifact = ItineraryCollectionArtifact(
                title=(f"{destination}保存的方案" if destination else "保存的旅行方案"),
                match_kind=match_kind,
                items=[ItineraryCard.model_validate(item) for item in chosen],
            ).model_dump(mode="json")
        return ({
            "exact_matches": [self._model_itinerary(item) for item in exact[:bounded_limit]],
            "near_matches": [self._model_itinerary(item) for item in near[:bounded_limit]],
            "displayed_match_kind": match_kind if chosen else None,
            "count": len(chosen),
        }, artifact)

    def get_saved_itinerary(
        self, context: MainAgentContext, itinerary_id: str, day: int | None = None
    ) -> dict[str, Any]:
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT id,parent_id,root_id,version,destination,start_date,end_date,"
                "created_at,modification_notes,plan_json FROM itineraries "
                "WHERE id=? AND user_id=?",
                (itinerary_id, context.user_id),
            ).fetchone()
        if not row:
            raise OwnedResourceNotFound("itinerary not found")
        item = self._itinerary_row(row)
        plan = json.loads(row["plan_json"] or "{}")
        summary = self._model_itinerary(item)
        if day is None:
            return {"summary": summary, "highlights": item["highlights"]}
        if day < 1:
            raise ValueError("day must be at least 1")
        days = plan.get("days") or plan.get("itinerary") or []
        selected = next((entry for index, entry in enumerate(days, 1) if int(entry.get("day", index)) == day), None)
        if selected is None:
            raise ValueError("requested day not found")
        return {"summary": summary, "day": selected}

    async def update_current_brief(
        self, context: MainAgentContext, patch: dict[str, Any]
    ) -> AgentToolResult:
        run = self._chat_run(context)
        brief, error = await self.chat.actions.apply_brief_patch(run, patch)
        return AgentToolResult(
            ok=brief is not None, code="ok" if brief else "brief_validation_failed",
            message=error or "旅行需求已更新。",
            data={"brief": self.chat._brief_context(brief)} if brief else {},
        )

    async def submit_current_brief(self, context: MainAgentContext) -> AgentToolResult:
        run = self._chat_run(context)
        brief = self.chat.briefs.active_for_conversation(
            context.user_id, context.conversation_id
        )
        if not brief:
            brief = self.chat.briefs.latest_for_conversation(
                context.user_id, context.conversation_id
            )
        if brief and brief.get("status") == "submitted" and run.get("result_itinerary_id"):
            itinerary_id = str(run["result_itinerary_id"])
            return AgentToolResult(
                ok=True, message="完整行程已经生成。",
                data={"brief_id": brief["id"], "itinerary_id": itinerary_id},
            )
        missing = required_brief_fields((brief or {}).get("data") or {})
        if missing:
            try:
                answer = interrupt(required_input_interrupt(missing))
            except RuntimeError:
                return AgentToolResult(
                    ok=False, code="brief_not_ready",
                    message="当前需求还缺少必要信息。",
                    data={"missing_fields": missing},
                )
            return AgentToolResult(
                ok=False,
                code="required_input_received",
                message="用户补充了必要信息。请解析后更新当前 Brief，并再次提交；如果仍缺字段，继续收集。",
                data={"answer": answer, "previous_missing_fields": missing},
            )
        if not brief:
            return AgentToolResult(ok=False, code="brief_not_ready", message="当前还没有可提交的旅行需求。")
        submitted, itinerary_id = await self.chat.execute_planner_tool(run, brief["id"])
        return AgentToolResult(
            ok=True, message="完整行程已经生成。",
            data={"brief_id": submitted["id"], "itinerary_id": itinerary_id},
        )

    async def start_revision(
        self, context: MainAgentContext, itinerary_id: str, modification_notes: str
    ) -> AgentToolResult:
        run = self._chat_run(context)
        notes = modification_notes
        for _attempt in range(3):
            result_id, concern = await self.chat.execute_revision_tool(
                run, itinerary_id, notes
            )
            if result_id:
                return AgentToolResult(
                    ok=True, message="行程修改已经完成。",
                    data={"itinerary_id": result_id},
                )
            if not concern:
                break
            answer = interrupt({
                "question": str(concern.get("question") or "请补充这次修改的必要信息"),
                "missing_fields": list(concern.get("missing_fields") or ["modification_notes"]),
                "input_schema": concern.get("input_schema") or {"type": "string", "minLength": 1},
            })
            notes = f"{notes}\n【用户补充】{str(answer).strip()}"
        return AgentToolResult(ok=False, code="revision_rejected", message="修改信息仍不完整，请重新说明。")

    async def control_run(
        self, context: MainAgentContext, run_id: str, action: str
    ) -> AgentToolResult:
        run = self._chat_run(context)
        controlled, error = await self.chat.actions.control_run(run, run_id, action)
        return AgentToolResult(
            ok=controlled is not None, code="ok" if controlled else "run_control_rejected",
            message=error or "任务状态已更新。",
            data={"run": _public_run(controlled)} if controlled else {},
        )

    def _chat_run(self, context: MainAgentContext) -> dict[str, Any]:
        run = self.chat.runs.get(context.user_id, context.chat_run_id)
        if run["kind"] != RunKind.CHAT.value or run["conversation_id"] != context.conversation_id:
            raise OwnedResourceNotFound("chat run not found")
        return run

    @staticmethod
    def _matches_itinerary(
        item: dict[str, Any], *, destination: str | None, duration_days: int | None,
        travel_start_from: str | None, travel_start_to: str | None,
    ) -> bool:
        if destination and _clean_text(item["destination"]) != _clean_text(destination):
            return False
        if duration_days is not None and item["duration_days"] != duration_days:
            return False
        if travel_start_from and str(item.get("start_date") or "") < travel_start_from:
            return False
        if travel_start_to and str(item.get("start_date") or "") > travel_start_to:
            return False
        return True

    @staticmethod
    def _near_itinerary(
        item: dict[str, Any], *, destination: str | None, duration_days: int | None
    ) -> bool:
        destination_match = not destination or _clean_text(destination) in _clean_text(item["destination"])
        duration_match = duration_days is None or (
            item["duration_days"] is not None and abs(item["duration_days"] - duration_days) <= 1
        )
        return destination_match and duration_match

    @classmethod
    def _itinerary_row(cls, row: Any) -> dict[str, Any]:
        plan = json.loads(row["plan_json"] or "{}")
        root_id = row["root_id"] or row["id"]
        version = int(row["version"] or 1)
        duration = _date_days(row["start_date"], row["end_date"])
        if duration is None:
            days = plan.get("days") or plan.get("itinerary") or []
            duration = len(days) or None
        return {
            "itinerary_id": row["id"], "root_id": root_id, "version": version,
            "destination": row["destination"] or plan.get("destination") or "",
            "duration_days": duration, "start_date": row["start_date"] or None,
            "end_date": row["end_date"] or None, "created_at": row["created_at"] or "",
            "is_modified": bool(row["parent_id"] or row["modification_notes"] or version > 1),
            "highlights": cls._highlights(plan),
        }

    @staticmethod
    def _highlights(plan: dict[str, Any]) -> list[str]:
        values: list[str] = []
        for day in plan.get("days") or plan.get("itinerary") or []:
            entries = day.get("activities") or day.get("stops") or day.get("items") or []
            for entry in entries:
                name = entry.get("name") or entry.get("title") or entry.get("attraction")
                if name and str(name) not in values:
                    values.append(str(name)[:60])
                if len(values) >= 4:
                    return values
        return values

    @staticmethod
    def _model_itinerary(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item[key] for key in (
                "itinerary_id", "root_id", "version", "destination",
                "duration_days", "start_date", "end_date", "is_modified", "highlights",
            )
        }
