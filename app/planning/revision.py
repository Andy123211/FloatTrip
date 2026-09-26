"""Revision-only preparation: current route, bounded POI discovery and user choices."""
from __future__ import annotations

import asyncio
import re
import os
from typing import Any

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.llm.factory import build_structured_llm
from app.planning.candidate_builder import excluded_poi_names
from app.planning.helpers import amap_key, ainvoke_structured, parse_iso_date
from app.planning.schemas import TravelPlanState
from app.providers.amap.poi import poi_to_spot, search_attraction_pois_async


class RevisionSearchFailure(RuntimeError):
    public_code = "revision_search_failed"
    public_message = "暂时无法查找适合的新景点，请稍后重试；原行程仍然保留。"
    public_retryable = True


class RevisionCandidatesUnavailable(RevisionSearchFailure):
    public_code = "revision_candidates_unavailable"
    public_message = "暂时没有找到能够满足这次要求的景点方案，原行程仍然保留。可以重试，或停止后调整要求。"


class RevisionSearchIntent(BaseModel):
    needs_search: bool = False
    queries: list[str] = Field(default_factory=list, max_length=6)
    explicit_places: list[str] = Field(default_factory=list, max_length=12)
    excluded_places: list[str] = Field(default_factory=list, max_length=12)


def current_revision_base(base: dict[str, Any], notes: str):
    """Project the displayed saved timeline, never the stale planner route."""
    plan = base["plan"]
    checkpoint = base.get("planner_state") or {}
    route, current = [], {}
    for index, day in enumerate(plan.get("days") or [], 1):
        spots = []
        for item in day.get("timeline") or []:
            if item.get("type") != "attraction" or not item.get("name"):
                continue
            start = str(item.get("start_time") or item.get("start") or "09:00")
            period = item.get("period") or ("morning" if start < "12:00" else "afternoon" if start < "18:00" else "evening")
            spots.append({"name": item["name"], "start_time": start,
                          "end_time": item.get("end_time") or item.get("end") or "11:00",
                          "period": period, "reason": item.get("reason") or ""})
            current[item["name"]] = {**item, "open_time": item.get("open_time") or item.get("open")}
        route.append({"day": day.get("day", index), "theme": day.get("theme", ""), "spots": spots})
    old_names = {s["name"] for d in checkpoint.get("route") or [] for s in d.get("spots") or []}
    removed = {name for name in old_names - current.keys() if name not in notes}
    pois = {p["name"]: dict(p) for p in checkpoint.get("pois") or [] if p.get("name") not in removed}
    for name, item in current.items():
        # Preserve authoritative POI details while applying current manual changes.
        pois[name] = {**pois.get(name, {}), **{k: v for k, v in item.items() if v is not None}}
    return route, list(pois.values()), sorted(removed)


def make_revision_prepare_node(model_name=None):
    async def prepare(state: TravelPlanState):
        llm = build_structured_llm(RevisionSearchIntent, provider="deepseek", model=model_name or os.getenv("PLANNING_AGENT_MODEL"),
                                   temperature=0, thinking=True)
        result = await ainvoke_structured(llm, [("system",
            "分析行程修改是否需搜索新景点。替换/新增/热门地标/换景点类型需要搜索；仅改时间或顺序不需要。"
            "生成最多6条具体地点或类型搜索词，不返回整段要求。explicit_places、excluded_places只能引用用户原话中"
            "实际出现的地点名称，严禁把你推荐的地点当成用户点名。热门要求可提出城市代表地标搜索词。"),
            ("human", f"城市：{state.destination}\n用户原话：{state.revision_user_message or state.modification_notes}\n当前地点："
             + "、".join(s["name"] for d in state.route for s in d.get("spots", [])))])
        notes = state.revision_user_message or state.modification_notes or ""
        explicit = [name for name in result.explicit_places if name and name in notes]
        excluded = [name for name in result.excluded_places if name and name in notes]
        search = result.needs_search or bool(re.search(r"热门|冷门|地标|换.*景点|替换.*景点|增加.*景点", notes))
        queries = list(dict.fromkeys([*explicit, *result.queries]))[:6]
        if search and not queries:
            queries = [f"{state.destination}热门景点", f"{state.destination}城市地标"]
        return {"revision_needs_search": search, "revision_search_queries": queries,
                "revision_explicit_places": explicit,
                "revision_excluded_names": list(dict.fromkeys([*state.revision_excluded_names, *excluded]))}
    return prepare


async def revision_search_node(state: TravelPlanState):
    if state.revision_search_round >= 2:
        raise RevisionCandidatesUnavailable()
    known = {p["name"] for p in state.pois}
    unknown = [s["name"] for d in state.route for s in d.get("spots", []) if s["name"] not in known]
    queries = list(dict.fromkeys([*state.missing_places, *unknown, *state.revision_search_queries]))[:6]
    if not queries:
        queries = [f"{state.destination}热门景点", f"{state.destination}城市地标"]
    try:
        pages = await asyncio.gather(*(search_attraction_pois_async(
            state.destination or "", amap_key(), keywords=q, types="",
            page=2 if q in state.revision_searched_queries else 1,
        ) for q in queries))
    except Exception as exc:
        raise RevisionSearchFailure() from exc
    discovered = [spot for page in pages for raw in page if (spot := poi_to_spot(raw))]
    excluded = set(state.revision_excluded_names) | excluded_poi_names(
        [*state.pois, *discovered], state.effective_constraints)
    excluded = {name for name in excluded if name}
    # Match explicit abbreviated exclusions against provider-qualified names too.
    eligible = [p for p in [*state.pois, *discovered]
                if not any(name in p["name"] for name in excluded)]
    selected, seen_ids, seen_names = [], set(), set()
    protected = {s["name"] for d in state.route for s in d.get("spots", [])}
    discovered_names = {p["name"] for p in discovered}
    def priority(p):
        if p["name"] in protected or any(n in p["name"] for n in state.revision_explicit_places):
            return 0
        # New search results must not be crowded out by a full old candidate list.
        return 1 if p["name"] in discovered_names else 2
    for poi in sorted(eligible, key=priority):
        poi_id = poi.get("id")
        name_key = "".join(poi["name"].split()).casefold()
        if (poi_id and poi_id in seen_ids) or name_key in seen_names:
            continue
        if poi_id:
            seen_ids.add(poi_id)
        seen_names.add(name_key)
        selected.append(poi)
    return {"pois": selected[:60], "revision_search_round": state.revision_search_round + 1,
            "revision_needs_search": not any(p["name"] in discovered_names for p in selected[:60]), "modification_issue": "none",
            "revision_searched_queries": list(dict.fromkeys([*state.revision_searched_queries, *queries])),
            "modification_concern": None, "missing_places": [],
            "history": state.history + [f"已补充搜索景点，第{state.revision_search_round + 1}轮，验证{len(discovered)}个地点"]}


def revision_issue_kind(state: TravelPlanState) -> str:
    known = {p["name"] for p in state.pois}
    unknown = [s["name"] for d in state.route for s in d.get("spots", []) if s["name"] not in known]
    concern = state.modification_concern or ""
    if unknown or state.modification_issue == "candidate_gap" or re.search(r"候选池|扩充.*景点|未收录|checkpoint", concern, re.I):
        return "search"
    if state.modification_issue == "execution_failed":
        raise RevisionSearchFailure()
    return "choice" if concern or state.modification_issue == "needs_user_choice" else "review"


def revision_choice_node(state: TravelPlanState):
    question = state.modification_concern or "这次调整你更希望保留哪些安排？"
    answer = interrupt({"question": question, "missing_fields": ["modification_notes"],
                        "input_schema": {"type": "string", "minLength": 1}})
    notes = f"{state.modification_notes or ''}\n【用户补充】{str(answer).strip()}"
    return {"modification_concern": None, "modification_issue": "none",
            "modification_notes": notes,
            "revision_user_message": f"{state.revision_user_message}\n【用户补充】{str(answer).strip()}", "route_modify_opinion": f"【用户修改意见】{notes}"}


def revision_dates_node(state: TravelPlanState):
    start, end = state.travel_start_date, state.travel_end_date
    if start and end and end >= start:
        return {}
    answer = interrupt({"question": "这次旅行从哪天开始、哪天结束？", "missing_fields": ["start_date", "end_date"],
                        "input_schema": {"type": "string", "format": "date-range"}})
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", str(answer))
    start = parse_iso_date(dates[0]) if dates else None
    end = parse_iso_date(dates[1]) if len(dates) > 1 else None
    if not start or not end or end < start:
        # Loop within the graph to request a fresh interaction rather than fail a valid Run.
        return {"travel_start_date": None, "travel_end_date": None}
    return {"travel_start_date": start, "travel_end_date": end, "days": (end-start).days+1}
