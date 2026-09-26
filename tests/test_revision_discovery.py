from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import TypedDict
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt

from app.chat.service import ChatService
from app.core.database import get_conn, init_db
from app.core.memory import save_itinerary, load_itinerary, update_plan_json
from app.planning.revision import (
    RevisionCandidatesUnavailable, RevisionSearchFailure, current_revision_base,
    revision_issue_kind, revision_search_node, make_revision_prepare_node, RevisionSearchIntent,
)
from app.planning.runtime_worker import revision_snapshot_to_state
from app.planning.schemas import TravelPlanState
from app.runtime.manager import RunManager
from app.runtime.repositories import ConversationRepository


def poi(name="外滩", id="hot"):
    return {"id": id, "name": name, "location": "121.49,31.23", "type": "风景名胜", "biz_ext": {}}


def base_state(**updates):
    values = dict(query="上海一日游", destination="上海", days=1, travel_start_date="2026-10-01", travel_end_date="2026-10-01",
                  modification_notes="换成更加热门的景点", revision_search_queries=["外滩"],
                  pois=[{"id":"old","name":"老景点","location":{"lng":121.4,"lat":31.2}}])
    return TravelPlanState(**(values | updates))


class RevisionDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_landmark_without_rating_is_retained_and_deduplicated(self):
        state = base_state(revision_excluded_names=["东方明珠"])
        search = AsyncMock(return_value=[poi(), poi(), poi("东方明珠广播电视塔", "excluded")])
        with patch("app.planning.revision.search_attraction_pois_async", search), patch("app.planning.revision.amap_key", return_value="test"):
            result = await revision_search_node(state)
        self.assertEqual([p["name"] for p in result["pois"]], ["外滩", "老景点"])
        self.assertIsNone(result["pois"][0]["rating"])
        self.assertEqual(result["revision_search_round"], 1)

    async def test_new_results_survive_full_old_pool_and_excluded_results_retry(self):
        old = [{"id": str(i), "name": f"原候选{i}"} for i in range(60)]
        search = AsyncMock(return_value=[poi()])
        with patch("app.planning.revision.search_attraction_pois_async", search), patch("app.planning.revision.amap_key", return_value="test"):
            result = await revision_search_node(base_state(pois=old))
            self.assertEqual(len(result["pois"]), 60)
            self.assertIn("外滩", [p["name"] for p in result["pois"]])
            excluded = await revision_search_node(base_state(revision_excluded_names=["外滩"]))
            self.assertTrue(excluded["revision_needs_search"])

    async def test_provider_error_is_retryable_failure_not_a_question(self):
        with patch("app.planning.revision.search_attraction_pois_async", AsyncMock(side_effect=OSError("network"))), patch("app.planning.revision.amap_key", return_value="test"):
            with self.assertRaises(RevisionSearchFailure):
                await revision_search_node(base_state())
        with self.assertRaises(RevisionCandidatesUnavailable):
            await revision_search_node(base_state(revision_search_round=2))

    async def test_empty_results_are_bounded_to_two_rounds(self):
        search=AsyncMock(return_value=[])
        with patch("app.planning.revision.search_attraction_pois_async",search),patch("app.planning.revision.amap_key",return_value="test"):
            state=base_state()
            for _ in range(2):
                state=state.model_copy(update=await revision_search_node(state))
                self.assertTrue(state.revision_needs_search)
            with self.assertRaises(RevisionCandidatesUnavailable):
                await revision_search_node(state)
        self.assertEqual(search.await_count,2)

    async def test_only_user_evidenced_names_are_treated_as_explicit(self):
        intent=RevisionSearchIntent(needs_search=True,queries=["外滩"],explicit_places=["外滩"])
        with patch("app.planning.revision.build_structured_llm",return_value=object()),patch("app.planning.revision.ainvoke_structured",AsyncMock(return_value=intent)):
            result=await make_revision_prepare_node()(base_state(revision_user_message="换成热门景点",modification_notes="用户点名外滩"))
        self.assertEqual(result["revision_explicit_places"],[])
        self.assertTrue(result["revision_needs_search"])
        intent=RevisionSearchIntent(needs_search=False)
        with patch("app.planning.revision.build_structured_llm",return_value=object()),patch("app.planning.revision.ainvoke_structured",AsyncMock(return_value=intent)):
            result=await make_revision_prepare_node()(base_state(modification_notes="第一天晚一小时出发"))
        self.assertFalse(result["revision_needs_search"])

    def test_unknown_route_and_legacy_pool_concern_trigger_search(self):
        state=base_state(route=[{"day":1,"spots":[{"name":"不存在的地点"}]}])
        self.assertEqual(revision_issue_kind(state),"search")
        self.assertEqual(revision_issue_kind(base_state(modification_concern="候选池未收录地标")),"search")
        self.assertEqual(revision_issue_kind(base_state(modification_issue="needs_user_choice",modification_concern="想保留哪个预约？")),"choice")

    def test_current_saved_route_overrides_stale_checkpoint(self):
        base={"plan":{"days":[{"day":1,"theme":"手动主题","timeline":[{"type":"attraction","name":"手动新增","start_time":"11:00","end_time":"12:00","location":{"lng":121,"lat":31}}]}]},
              "planner_state":{"route":[{"spots":[{"name":"已删除"}]}],"pois":[{"name":"已删除"}]}}
        route,pois,removed=current_revision_base(base,"节奏慢一点")
        self.assertEqual(route[0]["spots"][0]["name"],"手动新增")
        self.assertEqual(route[0]["spots"][0]["start_time"],"11:00")
        self.assertEqual(removed,["已删除"])
        self.assertNotIn("已删除",[p["name"] for p in pois])


class RevisionCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_revision_resumes_without_research_and_saves_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"test.db";init_db(db)
            with get_conn(db) as conn:
                conn.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES('owner','owner','hash','2026-01-01')")
                parent=save_itinerary("owner",{"destination":"上海","start_date":"2026-10-01","end_date":"2026-10-01","days":[{"day":1,"timeline":[{"type":"attraction","name":"老景点","start_time":"09:00","end_time":"11:00","location":{"lng":121.4,"lat":31.2}}]}]},"上海",conn,planner_state={"pois":[],"route":[]})
            manager=RunManager(db);service=ChatService(manager,db)
            conversation=ConversationRepository(db).create("owner","修改")
            _,run=await service.submit_message("owner",conversation["id"],"换成热门景点",related_itinerary_id=parent)
            class ParentState(TypedDict):
                result: str
            async def call_revision(_state):
                result,_=await service.execute_revision_tool(run,parent,"换成热门景点")
                return {"result":result}
            async def prepare(state):
                return {"revision_needs_search":True,"revision_search_queries":["外滩"]}
            async def planner(state):
                route=[{"day":1,"theme":"滨江漫游","spots":[{"name":"外滩","start_time":"09:00","end_time":"11:00","period":"morning"}]}]
                if "用户补充" not in (state.modification_notes or ""):
                    return {"route":route,"modification_issue":"needs_user_choice","modification_concern":"想上午还是下午看江景？"}
                return {"route":route,"modification_issue":"none","modification_concern":None}
            async def reviewer(state): return {"approved":True}
            async def empty(state): return {}
            search=AsyncMock(return_value=[poi()]);prepare_mock=AsyncMock(side_effect=prepare)
            def parent_graph(saver):
                graph=StateGraph(ParentState);graph.add_node("modify",call_revision);graph.add_edge(START,"modify");graph.add_edge("modify",END)
                return graph.compile(checkpointer=saver)
            with patch("app.planning.revision.make_revision_prepare_node",return_value=prepare_mock), \
                 patch("app.planning.graph.make_planner_node",return_value=planner), \
                 patch("app.planning.graph.make_reviewer_node",return_value=reviewer), \
                 patch("app.planning.graph.meal_search_node",empty), \
                 patch("app.planning.graph.make_meal_recommend_node",return_value=empty), \
                 patch("app.planning.revision.search_attraction_pois_async",search), \
                 patch("app.planning.revision.amap_key",return_value="test"), \
                 patch("app.planning.runtime_worker.launch_shadow_profiles"), \
                 patch("app.planning.runtime_worker._launch_silent_tip_task"):
                saver=InMemorySaver();config={"configurable":{"thread_id":"test"}}
                graph=parent_graph(saver)
                result=await graph.ainvoke({},config)
                self.assertTrue(result.get("__interrupt__"))
                self.assertEqual(search.await_count,1)
                # Rebuild the parent as on reconnect; child state survives in its checkpoint.
                result=await parent_graph(saver).ainvoke(Command(resume="上午"),config)
                self.assertEqual(search.await_count,1)
                self.assertEqual(prepare_mock.await_count,1)
                with get_conn(db) as conn:
                    new=load_itinerary(result["result"],conn)
                    self.assertEqual(new["parent_id"],parent)
                    self.assertEqual(new["version"],2)
                    self.assertEqual(new["plan"]["days"][0]["timeline"][0]["name"],"外滩")
                    self.assertIsNotNone(load_itinerary(parent,conn))
                # Upgrade a pre-change top-level tool interrupt with no child checkpoint.
                async def old_tool(_state):
                    interrupt({"question":"候选池不足，请补充", "input_schema":{"type":"string"}})
                    return {"result":"old"}
                old=StateGraph(ParentState);old.add_node("modify",old_tool);old.add_edge(START,"modify");old.add_edge("modify",END)
                legacy_config={"configurable":{"thread_id":"legacy"}}
                paused=await old.compile(checkpointer=saver).ainvoke({},legacy_config)
                interaction_id=paused["__interrupt__"][0].id
                with get_conn(db) as conn:
                    conn.execute("UPDATE runs SET status='waiting_user',outstanding_interaction_id=? WHERE id=?",(interaction_id,run["id"]))
                manager.runs.accept_interaction("owner",run["id"],interaction_id,"按原要求继续")
                resumed=await parent_graph(saver).ainvoke(Command(resume="按原要求继续"),legacy_config)
                self.assertTrue(resumed["result"])
                self.assertIn("按原要求继续",prepare_mock.call_args.args[0].revision_user_message)

            mismatch=run | {"request_snapshot":{"related_itinerary_id":"other"}}
            with self.assertRaises(RevisionSearchFailure) as err:
                await service.execute_revision_tool(mismatch,parent,"修改")
            self.assertEqual(err.exception.public_code,"revision_target_mismatch")
