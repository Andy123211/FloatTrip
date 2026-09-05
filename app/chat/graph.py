"""Single LLM-powered conversation understanding graph."""

from __future__ import annotations

import logging
import os
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.chat.models import DialogueDecision, DialogueUnderstandingError
from app.core.planning_brief import required_brief_fields, required_input_interrupt
from app.chat.prompts import dialogue_messages
from app.llm.factory import build_structured_llm
from app.planning.helpers import ainvoke_structured


logger = logging.getLogger(__name__)


class ChatState(TypedDict, total=False):
    dialogue_context: dict[str, Any]
    decision: dict[str, Any]
    response: str
    pending_brief: dict[str, Any]
    planning_requested: bool
    planning_handoff: bool


async def dialogue_agent_node(
    state: ChatState,
    *,
    llm: Any | None = None,
) -> dict[str, Any]:
    """Call the structured LLM once, with one safe schema-repair attempt."""
    client = llm or build_structured_llm(
        DialogueDecision,
        provider="deepseek",
        model=os.getenv("MAIN_AGENT_MODEL") or None,
        temperature=0,
        thinking=True,
        reasoning_effort=os.getenv("MAIN_AGENT_REASONING_EFFORT", "high"),
    )
    context = state.get("dialogue_context") or {}
    messages = dialogue_messages(context)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            result = await ainvoke_structured(client, messages, retries=1)
            decision = (
                result
                if isinstance(result, DialogueDecision)
                else DialogueDecision.model_validate(result)
            )
            return {
                "decision": decision.model_dump(mode="json"),
                "response": decision.reply,
            }
        except Exception as exc:  # schema/provider errors are intentionally opaque
            last_error = exc
            if attempt == 0:
                messages = [
                    *messages,
                    HumanMessage(
                        "上一份结构化结果未通过校验。请只按既定 schema 重新输出，"
                        "不要添加字段，也不要解释错误。",
                    ),
                ]
    error_name = type(last_error).__name__ if last_error else "UnknownError"
    logger.warning("Dialogue understanding failed after schema repair: %s", error_name)
    if error_name in {"APIConnectionError", "APITimeoutError"}:
        raise DialogueUnderstandingError(
            "暂时无法连接 AI 服务，请检查网络或代理配置后重试。",
            code="llm_connection_failed",
        ) from last_error
    if error_name in {"AuthenticationError", "PermissionDeniedError"}:
        raise DialogueUnderstandingError(
            "AI 服务认证失败，请检查 DeepSeek API Key 配置。",
            code="llm_authentication_failed",
        ) from last_error
    if error_name in {"BadRequestError", "NotFoundError", "UnprocessableEntityError"}:
        raise DialogueUnderstandingError(
            "AI 服务请求被拒绝，请检查 DeepSeek 模型和服务地址配置。",
            code="llm_request_rejected",
        ) from last_error
    raise DialogueUnderstandingError() from last_error


def build_chat_graph(checkpointer=None, *, llm: Any | None = None):
    graph = StateGraph(ChatState)

    async def dialogue_agent(state: ChatState) -> dict[str, Any]:
        return await dialogue_agent_node(state, llm=llm)

    def accumulate_brief(state: ChatState) -> dict[str, Any]:
        decision = DialogueDecision.model_validate(state.get("decision") or {})
        context = state.get("dialogue_context") or {}
        existing = (
            state.get("pending_brief")
            or ((context.get("application_state") or {}).get("planning_brief") or {}).get("data")
            or {}
        )
        pending = dict(existing)
        patch = decision.brief_patch.model_dump(exclude_none=True, exclude_unset=True)
        if patch.get("trip_constraints"):
            patch["trip_constraints"] = [
                *(pending.get("trip_constraints") or []),
                *patch["trip_constraints"],
            ]
        pending.update(patch)
        planning_requested = bool(state.get("planning_requested")) or decision.intent in {
            "create_plan", "confirm_plan"
        }
        missing = required_brief_fields(pending) if planning_requested else []
        return {
            "pending_brief": pending,
            "planning_requested": planning_requested,
            "planning_handoff": planning_requested and not missing,
        }

    def collect_required_input(state: ChatState) -> dict[str, Any]:
        missing = required_brief_fields(state.get("pending_brief") or {})
        answer = interrupt(required_input_interrupt(missing))
        context = dict(state.get("dialogue_context") or {})
        application_state = dict(context.get("application_state") or {})
        planning_brief = dict(application_state.get("planning_brief") or {})
        planning_brief["data"] = dict(state.get("pending_brief") or {})
        planning_brief["missing_fields"] = missing
        application_state["planning_brief"] = planning_brief
        context["application_state"] = application_state
        context["current_message"] = str(answer).strip()
        return {
            "dialogue_context": context,
            "decision": {},
            "response": "",
            "planning_handoff": False,
        }

    def route_after_accumulate(state: ChatState) -> str:
        if not state.get("planning_requested"):
            return "done"
        return "done" if state.get("planning_handoff") else "collect_required_input"

    graph.add_node("dialogue_agent", dialogue_agent)
    graph.add_node("accumulate_brief", accumulate_brief)
    graph.add_node("collect_required_input", collect_required_input)
    graph.add_edge(START, "dialogue_agent")
    graph.add_edge("dialogue_agent", "accumulate_brief")
    graph.add_conditional_edges(
        "accumulate_brief",
        route_after_accumulate,
        {"collect_required_input": "collect_required_input", "done": END},
    )
    graph.add_edge("collect_required_input", "dialogue_agent")
    return graph.compile(checkpointer=checkpointer)
