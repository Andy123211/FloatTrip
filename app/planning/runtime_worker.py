"""Adapter from immutable travel-plan Run snapshots to TravelPlanState."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.core.database import get_conn
from app.core.memory import (
    load_itinerary,
    save_itinerary,
)
from app.core.travel_memory import MemoryRepository
from app.core.planning_constraints import constraints_for_prompt
from app.planning.schemas import TravelPlanState
from app.planning.shadow_profiles import launch_shadow_profiles
from app.planning.restaurant_enrichment import route_fingerprint
from app.planning.tip_enrichment import generate_tip_enrichment, save_tip_enrichment
from app.runtime.models import PublicError, RunKind, RunStatus
from app.runtime.manager import RunManager
from app.runtime.repositories import ConversationRepository


def itinerary_completion_message(destination: str, plan: dict) -> str:
    summary = plan.get('validation_summary') or {}
    if summary.get('status') == 'failed':
        return f'{destination}的行程未通过需求核验，请查看未满足的要求。'
    if summary.get('status') == 'needs_verification':
        return f'{destination}的行程已生成，部分开放时间或转场仍待核实，具体项目已列在方案中。'
    if summary.get('status') == 'supported_checks_passed':
        return f'{destination}的行程已生成，并通过当前支持的需求与排程检查。预约余量和出行当天情况仍需确认。'
    return f'{destination}的行程已生成，可以打开查看安排和出行注意事项。'


def snapshot_to_state(snapshot: dict[str, Any]) -> TravelPlanState:
    allowed = set(TravelPlanState.model_fields)
    values = {key: value for key, value in snapshot.items() if key in allowed}
    values.setdefault("travel_start_date", snapshot.get("start_date"))
    values.setdefault("travel_end_date", snapshot.get("end_date"))
    if not values.get("query"):
        destination = values.get("destination") or snapshot.get("destination") or ""
        days = values.get("days") or snapshot.get("days") or ""
        values["query"] = f"{destination}{days}日游".strip()
    return TravelPlanState(**values)


async def planning_run_to_state(run: dict[str, Any]) -> TravelPlanState:
    facts = run["request_snapshot"].get("memory_profile_snapshot")
    if facts is None:
        _revision, facts = await asyncio.to_thread(
            MemoryRepository().snapshot, run["user_id"]
        )
    effective = run["request_snapshot"].get("effective_constraints") or []
    snapshot = {
        **run["request_snapshot"],
        "profile_hint": (
            constraints_for_prompt(effective)
            if effective else MemoryRepository.format_for_prompt(facts)
        ) or None,
    }
    return snapshot_to_state(snapshot)


async def revision_snapshot_to_state(run: dict[str, Any], db_path=None) -> TravelPlanState:
    snapshot = run["request_snapshot"]
    parent_id = snapshot.get("related_itinerary_id") or snapshot.get("parent_plan_id")

    def load():
        with get_conn(db_path) as conn:
            return load_itinerary(parent_id, conn) if parent_id else None

    base = await asyncio.to_thread(load)
    if not base:
        raise ValueError("基础行程不存在")
    checkpoint = base.get("planner_state") or {}
    from app.planning.revision import current_revision_base
    current_route, current_pois, removed = current_revision_base(base, snapshot.get("revision_user_message") or snapshot.get("modification_notes", ""))
    plan = base["plan"]
    return TravelPlanState(
        query=checkpoint.get("query", "修改行程"),
        route=current_route,
        pois=current_pois,
        revision_excluded_names=removed,
        revision_base_plan=plan,
        revision_user_message=snapshot.get("revision_user_message") or snapshot.get("modification_notes", ""),
        planner_reviewer_dialogue=checkpoint.get("planner_reviewer_dialogue", []),
        destination=plan.get("destination") or checkpoint.get("destination"),
        travel_start_date=plan.get("start_date") or checkpoint.get("travel_start_date"),
        travel_end_date=plan.get("end_date") or checkpoint.get("travel_end_date"),
        days=len(plan.get("days") or []) or checkpoint.get("days", 0),
        attraction_preference=checkpoint.get("attraction_preference"),
        food_preference=checkpoint.get("food_preference"),
        habit_preference=checkpoint.get("habit_preference"),
        trip_budget=snapshot.get("trip_budget") or checkpoint.get("trip_budget"),
        effective_constraints=(
            snapshot.get("effective_constraints")
            or checkpoint.get("effective_constraints")
            or []
        ),
        constraint_coverage=(
            snapshot.get("constraint_coverage")
            or checkpoint.get("constraint_coverage")
            or []
        ),
        weather_forecast=checkpoint.get("weather_forecast", []),
        weather_note=checkpoint.get("weather_note"),
        max_per_day=checkpoint.get("max_per_day", 3),
        route_modify_opinion=f"【用户修改意见】{snapshot.get('modification_notes', '')}",
        modification_notes=snapshot.get("modification_notes"),
        parent_plan_id=parent_id,
        max_review_rounds=2,
    )


class PlanningFinalizer:
    def __init__(
        self, manager: RunManager, on_tip_queued: Callable[[], None] | None = None,
        *, tips_as_run: bool = True,
    ):
        self.manager = manager
        self.on_tip_queued = on_tip_queued
        self.tips_as_run = tips_as_run

    async def __call__(
        self,
        run: dict[str, Any],
        updates: dict[str, Any],
        _assistant_text: str,
    ) -> dict[str, Any] | None:
        state = TravelPlanState(
            **{
                **snapshot_to_state(run["request_snapshot"]).model_dump(),
                **updates,
            }
        )
        if not state.final_plan:
            raise RuntimeError("planning graph completed without an itinerary")
        itinerary_id = await asyncio.to_thread(self._persist, run, state)
        # Shadow profiles reuse the frozen pool but never delay the formal run.
        launch_shadow_profiles(itinerary_id, run["user_id"], state)
        await self.manager.publish(
            run["id"],
            "custom",
            {
                "kind": "planning.itinerary_created",
                "itinerary_id": itinerary_id,
                "destination": str(state.destination or ""),
            },
            durable=True,
        )
        if run.get("conversation_id"):
            destination = str(state.destination or "这趟旅行")
            message = await asyncio.to_thread(
                ConversationRepository(self.manager.db_path).add_message,
                run["user_id"], run["conversation_id"], "assistant",
                itinerary_completion_message(destination, state.final_plan),
                related_run_id=run["id"], related_itinerary_id=itinerary_id,
            )
            await self.manager.publish(
                run["id"], "custom", {
                    "kind": "chat.message.completed",
                    "message_id": message["id"],
                    "content": message["content"],
                    "sequence": message["sequence"],
                    "created_at": message["created_at"],
                    "artifacts": message["artifacts"],
                    "related_itinerary_id": itinerary_id,
                }, durable=True, validate_custom=False,
            )
        if self.tips_as_run:
            self.manager.create(
                user_id=run["user_id"], kind=RunKind.SPOT_TIPS,
                conversation_id=run.get("conversation_id"), itinerary_id=itinerary_id,
                request_snapshot={"itinerary_id": itinerary_id, "route_fingerprint": route_fingerprint(state.final_plan)},
            )
            if self.on_tip_queued:
                self.on_tip_queued()
            await self.manager.publish_itinerary(
                itinerary_id,
                {"kind": "itinerary.tip_status_changed", "itinerary_id": itinerary_id, "status": "queued"},
            )
        else:
            _launch_silent_tip_task(
                self.manager, itinerary_id, run["user_id"],
                state.final_plan, state.model_dump(mode="json"),
            )
        return {"result_itinerary_id": itinerary_id}

    def _persist(self, run: dict[str, Any], state: TravelPlanState) -> str:
        checkpoint = {
            key: value
            for key, value in state.model_dump(mode="json").items()
            if key != "final_plan"
        }
        with get_conn(self.manager.db_path) as conn:
            itinerary_id = save_itinerary(
                run["user_id"],
                state.final_plan,
                state.query,
                conn,
                parent_id=state.parent_plan_id,
                modification_notes=state.modification_notes,
                planner_state=checkpoint,
            )
        return itinerary_id


def _launch_silent_tip_task(
    manager: RunManager,
    itinerary_id: str,
    user_id: str,
    plan: dict[str, Any],
    planner_state: dict[str, Any],
) -> None:
    """Enrich tips without creating another public or persisted Run."""
    fingerprint = route_fingerprint(plan)

    async def work() -> None:
        await manager.publish_itinerary(
            itinerary_id,
            {"kind": "itinerary.tip_status_changed", "itinerary_id": itinerary_id, "status": "running"},
        )
        status = "failed"
        try:
            tips = await generate_tip_enrichment(plan, planner_state)
            with get_conn(manager.db_path) as conn:
                latest = load_itinerary(itinerary_id, conn)
                if latest and route_fingerprint(latest["plan"]) == fingerprint:
                    save_tip_enrichment(itinerary_id, user_id, fingerprint, tips, conn)
                    status = "succeeded"
                else:
                    status = "cancelled"
        except Exception:
            status = "failed"
        await manager.publish_itinerary(
            itinerary_id,
            {"kind": "itinerary.tip_status_changed", "itinerary_id": itinerary_id, "status": status},
        )

    asyncio.create_task(work(), name=f"spot-tips:{itinerary_id}")


class SpotTipsWorker:
    def __init__(self, manager: RunManager):
        self.manager = manager

    async def __call__(self, run: dict[str, Any], _cancel_event: asyncio.Event) -> None:
        itinerary_id = str(run["request_snapshot"].get("itinerary_id") or "")
        with get_conn() as conn:
            data = load_itinerary(itinerary_id, conn)
        if not data:
            raise ValueError("itinerary not found")
        plan = data["plan"]
        fingerprint = str(run["request_snapshot"].get("route_fingerprint") or "")
        if fingerprint != route_fingerprint(plan):
            await self.manager.transition(run["id"], RunStatus.CANCELLED)
            await self.manager.publish_itinerary(itinerary_id, {"kind": "itinerary.tip_status_changed", "itinerary_id": itinerary_id, "status": "cancelled"})
            return None
        await self.manager.publish_itinerary(itinerary_id, {"kind": "itinerary.tip_status_changed", "itinerary_id": itinerary_id, "status": "running"})
        try:
            tips = await generate_tip_enrichment(plan, data.get("planner_state") or {})
            written = False
            with get_conn() as conn:
                latest = load_itinerary(itinerary_id, conn)
                if latest and route_fingerprint(latest["plan"]) == fingerprint:
                    save_tip_enrichment(itinerary_id, run["user_id"], fingerprint, tips, conn)
                    written = True
            if written:
                status = "succeeded"
            else:
                await self.manager.transition(run["id"], RunStatus.CANCELLED)
                status = "cancelled"
        except Exception as exc:  # The enrichment must not poison the core itinerary run.
            await self.manager.transition(
                run["id"], RunStatus.FAILED,
                error_public=PublicError(
                    code="spot_tips_failed",
                    message="景点贴士暂未生成，可稍后手动重试",
                    retryable=True,
                ).model_dump(),
                error_internal=f"{type(exc).__name__}: {exc}",
            )
            status = "failed"
        await self.manager.publish_itinerary(itinerary_id, {"kind": "itinerary.tip_status_changed", "itinerary_id": itinerary_id, "status": status})
        return None
