"""Deterministic bounded scheduler for independently executing runs."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from app.runtime.manager import RunManager
from app.runtime.models import PublicError, RunKind, RunStatus
from app.runtime.observability import metrics

RunHandler = Callable[[dict[str, Any], asyncio.Event], Awaitable[dict[str, Any] | None]]


def public_error_for_exception(exc: Exception) -> PublicError:
    """Translate provider failures without exposing credentials or raw payloads."""
    explicit_code = getattr(exc, "public_code", None)
    explicit_message = getattr(exc, "public_message", None)
    if explicit_code or explicit_message:
        return PublicError(
            code=str(explicit_code or "run_failed"),
            message=str(explicit_message or "任务执行失败，请稍后重试"),
            retryable=bool(getattr(exc, "public_retryable", True)),
        )

    status_code = getattr(exc, "status_code", None)
    normalized = str(exc).casefold()
    if status_code == 402 or "insufficient balance" in normalized:
        return PublicError(
            code="llm_balance_exhausted",
            message="模型服务余额不足，请充值或切换可用模型后再试。",
            retryable=False,
        )
    if status_code in {401, 403}:
        return PublicError(
            code="llm_authentication_failed",
            message="模型服务认证失败，请检查 API Key 和模型权限。",
            retryable=False,
        )
    if status_code == 429:
        exhausted = "insufficient_quota" in normalized or "quota" in normalized
        return PublicError(
            code="llm_quota_exhausted" if exhausted else "llm_rate_limited",
            message=(
                "模型服务额度已用尽，请补充额度或切换可用模型后再试。"
                if exhausted
                else "模型服务当前请求过多，请稍后重试。"
            ),
            retryable=not exhausted,
        )
    return PublicError()


class RuntimeScheduler:
    def __init__(
        self,
        manager: RunManager,
        *,
        chat_limit: int = 8,
        planning_limit: int = 2,
        planning_per_user: int = 2,
        llm_limit: int = 8,
        amap_limit: int = 8,
    ):
        self.manager = manager
        self.chat_capacity = asyncio.Semaphore(max(1, chat_limit))
        self.planning_capacity = asyncio.Semaphore(max(1, planning_limit))
        self.planning_per_user = max(1, planning_per_user)
        self.llm_capacity = asyncio.Semaphore(max(1, llm_limit))
        self.amap_capacity = asyncio.Semaphore(max(1, amap_limit))
        self._user_planning: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.planning_per_user)
        )
        self._key_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._handlers: dict[RunKind, RunHandler] = {}
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task[Any] | None = None
        self._stopping = False

    def register(self, kind: RunKind | str, handler: RunHandler) -> None:
        self._handlers[RunKind(kind)] = handler

    def notify(self) -> None:
        self._wake.set()

    async def resume(
        self,
        run: dict[str, Any],
        interaction_id: str,
        value: Any,
    ) -> dict[str, Any]:
        handler = self._handlers.get(RunKind(run["kind"]))
        if not handler or not hasattr(handler, "resume"):
            raise ValueError("run handler does not support resume")
        resumed, accepted = await asyncio.to_thread(
            self.manager.runs.accept_interaction,
            run["user_id"], run["id"], interaction_id, value,
        )
        if not accepted:
            return resumed
        await self.manager.publish(
            run["id"], "custom", {"kind": "run.status", "status": "running"},
            durable=True,
        )
        cancel_event = asyncio.Event()
        task = asyncio.create_task(
            self._resume_execute(resumed, cancel_event, handler, value),
            name=f"resume:{run['id']}",
        )
        self.manager.register_task(run["id"], task, cancel_event)
        return resumed

    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        self._stopping = False
        await self.manager.reconcile_startup()
        self._loop_task = asyncio.create_task(self._loop(), name="agent-runtime-scheduler")
        self.notify()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._loop_task:
            await self._loop_task
        tasks = list(self.manager._tasks.values())
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=2
                )
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _loop(self) -> None:
        while not self._stopping:
            await self._wake.wait()
            self._wake.clear()
            for run in await asyncio.to_thread(self.manager.runs.queued):
                if run["id"] in self.manager._tasks:
                    continue
                cancel_event = asyncio.Event()
                task = asyncio.create_task(
                    self._execute(run, cancel_event), name=f"run:{run['id']}"
                )
                self.manager.register_task(run["id"], task, cancel_event)
            await asyncio.sleep(0)

    async def _execute(
        self, run: dict[str, Any], cancel_event: asyncio.Event
    ) -> None:
        kind = RunKind(run["kind"])
        handler = self._handlers.get(kind)
        if not handler:
            await self.manager.transition(
                run["id"],
                RunStatus.FAILED,
                error_public=PublicError(
                    code="handler_missing",
                    message="该任务类型暂不可用",
                    retryable=False,
                ).model_dump(),
                error_internal=f"no handler registered for {kind.value}",
            )
            return
        capacity = (
            self.chat_capacity
            if kind is RunKind.CHAT
            else self.planning_capacity
        )
        user_capacity = (
            self._user_planning[run["user_id"]]
            if kind in {RunKind.TRAVEL_PLAN, RunKind.REVISION, RunKind.SPOT_TIPS}
            else _NullAsyncContext()
        )
        key_lock = self._key_locks[run["concurrency_key"]]
        try:
            async with capacity, user_capacity, key_lock:
                fresh = await asyncio.to_thread(
                    self.manager.runs.get_internal, run["id"]
                )
                if fresh["status"] != RunStatus.QUEUED.value:
                    return
                await self.manager.transition(run["id"], RunStatus.RUNNING)
                metrics.run_started(fresh)
                result = await handler(fresh, cancel_event)
                latest = await asyncio.to_thread(
                    self.manager.runs.get_internal, run["id"]
                )
                if latest["status"] == RunStatus.RUNNING.value:
                    await self.manager.transition(
                        run["id"],
                        RunStatus.SUCCEEDED,
                        result_itinerary_id=(result or {}).get("result_itinerary_id"),
                    )
                    metrics.run_finished(fresh, RunStatus.SUCCEEDED.value)
                elif latest["status"] == RunStatus.WAITING_USER.value:
                    metrics.run_finished(fresh, RunStatus.WAITING_USER.value)
        except asyncio.CancelledError:
            latest = await asyncio.to_thread(self.manager.runs.get_internal, run["id"])
            if latest["status"] not in {
                RunStatus.CANCELLED.value,
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
            }:
                await self.manager.transition(run["id"], RunStatus.CANCELLED)
                metrics.run_finished(run, RunStatus.CANCELLED.value)
        except Exception as exc:  # noqa: BLE001
            latest = await asyncio.to_thread(self.manager.runs.get_internal, run["id"])
            if latest["status"] not in {
                RunStatus.CANCELLED.value,
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
            }:
                await self.manager.transition(
                    run["id"],
                    RunStatus.FAILED,
                    error_public=public_error_for_exception(exc).model_dump(),
                    error_internal=repr(exc),
                )
                metrics.run_finished(
                    run, RunStatus.FAILED.value, failure_reason=type(exc).__name__
                )
        finally:
            self.notify()

    async def _resume_execute(
        self,
        run: dict[str, Any],
        cancel_event: asyncio.Event,
        handler: Any,
        value: Any,
    ) -> None:
        kind = RunKind(run["kind"])
        capacity = self.chat_capacity if kind is RunKind.CHAT else self.planning_capacity
        user_capacity = (
            self._user_planning[run["user_id"]]
            if kind in {RunKind.TRAVEL_PLAN, RunKind.REVISION, RunKind.SPOT_TIPS}
            else _NullAsyncContext()
        )
        try:
            async with capacity, user_capacity, self._key_locks[run["concurrency_key"]]:
                metrics.run_started(run)
                result = await handler.resume(run, cancel_event, value)
                latest = await asyncio.to_thread(
                    self.manager.runs.get_internal, run["id"]
                )
                if latest["status"] == RunStatus.RUNNING.value:
                    await self.manager.transition(
                        run["id"],
                        RunStatus.SUCCEEDED,
                        result_itinerary_id=(result or {}).get("result_itinerary_id"),
                    )
                    metrics.run_finished(run, RunStatus.SUCCEEDED.value)
                elif latest["status"] == RunStatus.WAITING_USER.value:
                    metrics.run_finished(run, RunStatus.WAITING_USER.value)
        except asyncio.CancelledError:
            latest = await asyncio.to_thread(self.manager.runs.get_internal, run["id"])
            if latest["status"] not in {
                RunStatus.CANCELLED.value,
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
            }:
                await self.manager.transition(run["id"], RunStatus.CANCELLED)
                metrics.run_finished(run, RunStatus.CANCELLED.value)
        except Exception as exc:  # noqa: BLE001
            latest = await asyncio.to_thread(self.manager.runs.get_internal, run["id"])
            if latest["status"] not in {
                RunStatus.CANCELLED.value,
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
            }:
                await self.manager.transition(
                    run["id"],
                    RunStatus.FAILED,
                    error_public=public_error_for_exception(exc).model_dump(),
                    error_internal=repr(exc),
                )
                metrics.run_finished(
                    run, RunStatus.FAILED.value, failure_reason=type(exc).__name__
                )


class _NullAsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False
