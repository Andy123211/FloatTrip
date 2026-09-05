"""LangGraph 图构建与流水线入口。"""
from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from app.planning.schemas import TravelPlanState
from app.planning.nodes import (
    attraction_search_node,
    make_candidate_builder_node,
    make_finalize_node,
    make_meal_recommend_node,
    make_planner_node,
    make_reviewer_node,
    make_time_check_node,
    meal_search_node,
    optimize_attractions_node,
    quality_gate_node,
    route_after_quality_gate,
    route_after_planner,
    route_after_review,
    route_after_time_check,
    weather_lookup_node,
)


# ─── 构图 ────────────────────────────────────────────────────


def _with_progress(node_name: str, action):
    """Emit a stable public product event without exposing graph internals."""
    label = _NODE_LABELS.get(node_name, "正在处理")

    async def wrapped(state, config=None):
        writer = get_stream_writer()
        writer(
            {
                "kind": "planning_run.progress",
                "stage": node_name,
                "label": label,
            }
        )
        result = await asyncio.to_thread(action, state)
        if hasattr(result, "__await__"):
            return await result
        return result

    return wrapped


def _require_missing_input(state: TravelPlanState) -> dict[str, Any]:
    response = interrupt(
        {
            "question": "请补充：" + "、".join(state.missing_fields),
            "input_schema": {"type": "string", "minLength": 1},
        }
    )
    return {
        "query": f"{state.query}，{str(response).strip()}",
        "missing_fields": [],
    }

def build_graph(
    model_name: str | None = None,
    profile_hint: str = "",
    memory_writer=None,
    user_id: str | None = None,
    *,
    checkpointer=None,
    interrupt_on_missing: bool = False,
):
    g = StateGraph(TravelPlanState)

    g.add_node("weather_lookup", _with_progress("weather_lookup", weather_lookup_node))
    g.add_node("attraction_search", _with_progress("attraction_search", attraction_search_node))
    g.add_node("candidate_builder", _with_progress("candidate_builder", make_candidate_builder_node(model_name)))
    g.add_node("optimizer", _with_progress("optimizer", optimize_attractions_node))
    g.add_node("quality_gate", _with_progress("quality_gate", quality_gate_node))
    g.add_node("finalize", _with_progress("finalize", make_finalize_node(memory_writer)))
    g.add_edge(START, "weather_lookup")
    g.add_edge("weather_lookup", "attraction_search")
    g.add_edge("attraction_search", "candidate_builder")
    g.add_edge("candidate_builder", "optimizer")
    g.add_edge("optimizer", "quality_gate")
    g.add_conditional_edges(
        "quality_gate",
        route_after_quality_gate,
        {"candidate_builder": "candidate_builder", "finalize": "finalize"},
    )
    g.add_edge("finalize",       END)

    return g.compile(checkpointer=checkpointer)


# ─── 流水线入口 ───────────────────────────────────────────────


# ─── Runtime 进度标签与 Revision 图 ───────────────────────────

# 节点名 → 进度文案。同时充当“哪些事件需要透出”的过滤白名单。
# planner/reviewer 文案在运行时按轮次/通过位动态拼接，这里留占位。
_NODE_LABELS: dict[str, str] = {
    "weather_lookup":    "🌦 正在读取已确认需求并查询天气",
    "query_rewrite":     "🔎 正在结合用户画像改写查询",
    "intent":            "🧭 正在理解出行意图（目的地 / 日期 / 偏好）",
    "attraction_search": "🗺 正在调用高德搜索景点池",
    "candidate_builder": "🧩 正在生成候选景点旅游语义",
    "optimizer":         "🧮 正在确定性选择并优化景点路线",
    "quality_gate":      "✅ 正在独立复算与校验路线",
    "planner":           "✍️ 正在规划逐日行程",
    "reviewer":          "🔍 正在评审行程",
    "time_check":        "⏱ 正在核查景点开放时间",
    "meal_search":       "🍽 正在搜索周边餐厅",
    "meal_recommend":    "🍴 正在为每天挑选餐厅",
    "spot_tips":         "💡 正在为每个景点生成游玩贴士",
    "finalize":          "📦 正在收敛生成最终行程",
}


def _route_after_review_for_modification(state: TravelPlanState) -> str:
    """修改流程专用：reviewer 通过/达最大轮数 → 直接进 meal_search（不走 time_check）。"""
    if state.approved or state.review_round > state.max_review_rounds:
        return "meal_search"
    return "planner"


def _revision_concern_node(state: TravelPlanState) -> dict[str, Any]:
    if not state.modification_concern:
        return {}
    response = interrupt(
        {
            "question": state.modification_concern,
            "input_schema": {
                "type": "string",
                "description": "确认继续修改，或补充新的修改要求",
            },
        }
    )
    response_text = str(response).strip()
    return {
        "modification_concern": None,
        "route_modify_opinion": (
            state.route_modify_opinion
            if not response_text
            else f"{state.route_modify_opinion or ''}\n【用户确认/补充】{response_text}"
        ),
    }


def build_runtime_revision_graph(
    model_name: str | None = None,
    *,
    checkpointer=None,
):
    """Checkpointed revision graph using the same interrupt lifecycle as planning."""
    graph = StateGraph(TravelPlanState)
    graph.add_node("planner", _with_progress("planner", make_planner_node(model_name)))
    graph.add_node("revision_concern", _revision_concern_node)
    graph.add_node("reviewer", _with_progress("reviewer", make_reviewer_node(model_name)))
    graph.add_node("meal_search", _with_progress("meal_search", meal_search_node))
    graph.add_node(
        "meal_recommend",
        _with_progress("meal_recommend", make_meal_recommend_node(model_name)),
    )
    graph.add_node("finalize", _with_progress("finalize", make_finalize_node(None)))
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "revision_concern")
    graph.add_edge("revision_concern", "reviewer")
    graph.add_conditional_edges(
        "reviewer",
        _route_after_review_for_modification,
        {"planner": "planner", "meal_search": "meal_search"},
    )
    graph.add_edge("meal_search", "meal_recommend")
    graph.add_edge("meal_recommend", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
