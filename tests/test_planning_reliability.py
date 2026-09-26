from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock
import pytest

from app.planning.reliability import opening_day, opening_calendar, merge_candidates, is_facility
from app.planning.grounded import (Requirements, validation, choose_poi, forbidden,
    hydrate_transport, optimize)
from app.planning.schemas import OptimizerCandidate, TravelPlanState
from app.planning.optimizer import AttractionSubsetOptimizer, OptimizationFailure


@pytest.mark.parametrize('text', ['09:00-17:00；周二关闭','周二闭馆；周三至周日09:00-17:00','星期二不开放'])
def test_weekly_closure(text):
    assert opening_day(text,date(2026,10,13))['status']=='closed'


def test_season_entry_and_split_rules():
    raw='1月1日-10月14日09:00-18:00；10月15日-12月31日09:00-17:00，16:00停止入馆'
    assert opening_day(raw,date(2026,10,14))['intervals']==[[540,1080]]
    rule=opening_day(raw,date(2026,10,15))
    assert rule['intervals']==[[540,1020]] and rule['last_entry']==960
    assert opening_day('09:00-12:00；14:00-18:00',date(2026,10,13))['intervals']==[[540,720],[840,1080]]
    for text in [None,'以公告为准','周一闭馆，节假日除外','09:00-17:00；10:00-18:00','开放时间待定','09:99-18:00']:
        assert opening_day(text,date(2026,10,13))['status']=='unknown'


def test_provider_numeric_seasons_do_not_close_wrong_date():
    raw='07/01-08/31 周二 全天不开放；09/01-12/31 周二 09:00-17:00 最晚进入16:30'
    assert opening_day(raw,date(2026,10,13))=={'status':'known','intervals':[[540,1020]],'last_entry':990}
    raw='01-01至09-22 周一至周日 08:00-22:00；09-23至09-23 周一至周日 全天关闭；09-24至12-31 周一至周日 08:00-22:00'
    assert opening_day(raw,date(2026,10,13))['status']=='known'
    assert opening_day(raw,date(2026,9,23))['status']=='closed'


def candidate(name, **kwargs):
    return OptimizerCandidate(poi_name=name,poi_id=name,entity_id=name,location={'lng':120,'lat':30},
        **{'duration_min':60,'must_visit':True,**kwargs})


@pytest.mark.parametrize('budget',[0,1])
def test_solver_and_fallback_obey_split_opening_and_date(budget):
    cal=opening_calendar({'id':'x','open_time':'09:00-12:00；14:00-18:00；周二关闭'},date(2026,10,13),2)
    a=candidate('A',duration_min=180,earliest_start_min=660,opening_calendar=cal)
    solver=AttractionSubsetOptimizer(max_time_seconds=budget)
    with pytest.raises(OptimizationFailure):solver.solve([a],days=1,max_per_day=1,travel_start_date=date(2026,10,13))
    result=solver.solve([a],days=1,max_per_day=1,travel_start_date=date(2026,10,14))
    assert result.route[0]['spots'][0]['start_min']>=840
    assert result.quality_report.passed


def test_alias_merge_preserves_must_but_not_parent_merge():
    pool=[candidate('A',must_visit=False).model_dump(),candidate('A alias').model_dump(),candidate('child').model_dump()]
    pool[1]['poi_id']=pool[1]['entity_id']='A';pool[2]['parent_id']='A'
    out=merge_candidates(pool)
    assert len(out)==2 and out[0]['must_visit'] and out[1]['poi_name']=='child'
    assert 'A alias' in out[0]['entity_aliases']


def test_facility_and_category():
    assert is_facility('示例纪念馆保管部')
    assert choose_poi('示例纪念馆',[],[{'id':'x','name':'示例纪念馆保管部','location':'120,30'}],'示例市')[0] is None
    assert forbidden({'poi_name':'乐园','poi_type':'游乐场'},Requirements(forbidden_tags=['themepark']),'市')


def test_transport_direction_mode_and_rehydration():
    pool=[candidate('A').model_dump(),candidate('B').model_dump()]
    evidence=[{'from_id':'A','to_id':'B','requested_mode':'transit','duration_minutes':70,'buffer_minutes':15}]
    a,b=hydrate_transport(pool,evidence,'transit')
    assert a['transfer_minutes_to']=={'B':85} and b['transfer_minutes_to']=={}
    assert not hydrate_transport(pool,evidence,'driving')[0]['transfer_minutes_to']
    evidence[0]['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    assert not hydrate_transport(pool,evidence,'transit')[0]['transfer_minutes_to']


def test_final_output_duplicate_overlap_and_pending_are_distinct():
    c=candidate('A').model_dump()
    s=TravelPlanState(query='游览',days=1,candidate_pool=[c],hard_requirements={},
        route=[{'day':1,'spots':[{'name':'A','start_time':'10:00','end_time':'11:00'}]}])
    assert validation(s)['status']=='needs_verification'
    final={'days':[{'day':1,'timeline':[{'type':'attraction','name':'A','start_time':'10:00','end_time':'11:00'},
                                      {'type':'attraction','name':'A','start_time':'10:30','end_time':'11:30'}]}]}
    result=validation(s,final)
    assert result['status']=='failed'
    assert {'DUPLICATE_ENTITY:A','TIME_OVERLAP:A','FINAL_ROUTE_MISMATCH'}<=set(result['hard_violations'])


def test_repair_is_free_to_reorder_drop_optional_and_never_promote_must(monkeypatch):
    from app.planning import grounded,nodes
    pool=[candidate('A').model_dump(),candidate('B',must_visit=False).model_dump(),candidate('C',must_visit=False).model_dump()]
    calls=[]
    async def solve(state):
        calls.append(state)
        spots=[{'name':'A','start_time':'10:00','end_time':'11:00'}, {'name':'B','start_time':'11:20','end_time':'12:20'}] if len(calls)==1 else [{'name':'A','start_time':'10:00','end_time':'11:00'}]
        return {'route':[{'day':1,'spots':spots}],'quality_report':{'passed':True}}
    monkeypatch.setattr(nodes,'optimize_attractions_node',solve)
    monkeypatch.setattr(grounded,'transport_estimate',AsyncMock(return_value={'from':'A','to':'B','mode':'transit','status':'estimated','duration_minutes':100,'buffer_minutes':15}))
    state=TravelPlanState(query='去A',days=1,planning_variant='C',candidate_pool=pool,hard_requirements={'must_names':['A']})
    import asyncio
    result=asyncio.run(optimize(state))
    assert len(calls)==2
    assert len(calls[1].candidate_pool)==3
    assert not calls[1].candidate_pool[1]['must_visit'] and not calls[1].candidate_pool[1].get('fixed_day')
    assert calls[1].candidate_pool[0]['transfer_minutes_to']['B']==115
    assert len(result['route'][0]['spots'])==1


def test_completion_language():
    from app.planning.runtime_worker import itinerary_completion_message
    assert '待核实' in itinerary_completion_message('示例市',{'validation_summary':{'status':'needs_verification'}})
    assert '开放时间、餐饮' not in itinerary_completion_message('示例市',{})


def test_structured_failure_repairs_once_without_provider_retry(monkeypatch):
    import asyncio
    from app.planning import grounded, helpers, nodes
    from langchain_core.exceptions import OutputParserException
    monkeypatch.setattr(nodes,'build_structured_llm',lambda *a,**kw:object())
    invoke=AsyncMock(side_effect=[OutputParserException('bad shape'),Requirements()])
    monkeypatch.setattr(helpers,'ainvoke_structured',invoke)
    assert isinstance(asyncio.run(grounded.structured(Requirements,'extract',{})),Requirements)
    assert invoke.await_count==2
    invoke=AsyncMock(side_effect=ConnectionError('provider offline'))
    monkeypatch.setattr(helpers,'ainvoke_structured',invoke)
    with pytest.raises(ConnectionError):asyncio.run(grounded.structured(Requirements,'extract',{}))
    assert invoke.await_count==1


def test_budget_atomic_and_hits_need_no_reservation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from tests.travel_benchmark.budget import reserve, used, BudgetExhausted
    path=tmp_path/'budget.sqlite'
    def request(i):
        try:reserve(path,3,'trial','https://restapi.amap.com/redacted');return True
        except BudgetExhausted:return False
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(request,range(10)))
    assert sum(results)==3 and used(path)==3


def test_benchmark_submits_only_public_input(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from tests.travel_benchmark.runner import planning
    from app.planning import graph, runtime_worker
    captured={}
    def snapshot(value):captured.update(value);return value
    monkeypatch.setattr(runtime_worker,'snapshot_to_state',snapshot)
    monkeypatch.setattr(graph,'build_graph',lambda **kw:SimpleNamespace(ainvoke=AsyncMock(return_value={})))
    asyncio.run(planning({'input':{'query':'上海三日游'},'expectations':{'secret':'REFERENCE_ANSWER'},'catalog':['ANSWER']},SimpleNamespace(record={})))
    assert captured=={'query':'上海三日游'}


def test_popularity_group_does_not_imply_duplicate_visit():
    from tests.travel_benchmark.evidence import Catalog
    cat=Catalog({'attractions':[{'id':'district','city':'城','name':'街区','aliases':['地标楼'],
        'visit_identity_by_alias':{'街区':'street','地标楼':'building'}}]})
    assert cat.match('街区','城')['visit_identity']!=cat.match('地标楼','城')['visit_identity']
    assert cat.group(cat.match('街区','城')['id'])==cat.group(cat.match('地标楼','城')['id'])


@pytest.mark.parametrize('budget',[0,1])
def test_calendar_can_move_must_to_other_day(budget):
    start=date(2026,10,13)
    a=candidate('A',opening_calendar=opening_calendar({'open_time':'09:00-17:00；周二关闭'},start,2))
    b=candidate('B',opening_calendar=opening_calendar({'open_time':'09:00-17:00；周三关闭'},start,2))
    result=AttractionSubsetOptimizer(max_time_seconds=budget).solve([a,b],days=2,max_per_day=1,travel_start_date=start)
    assert result.route[0]['spots'][0]['name']=='B'
    assert result.route[1]['spots'][0]['name']=='A'


def test_nonempty_invalid_route_does_not_end_transport_repair(monkeypatch):
    import asyncio
    from app.planning import grounded,nodes
    calls=[]
    async def solve(state):
        calls.append(state)
        return {'route':[{'day':1,'spots':[{'name':'A','start_time':'10:00','end_time':'11:00'}]}],
                'quality_report':{'passed':len(calls)>=3}}
    monkeypatch.setattr(nodes,'optimize_attractions_node',solve)
    state=TravelPlanState(query='去A',days=1,planning_variant='C',candidate_pool=[candidate('A').model_dump()])
    result=asyncio.run(optimize(state))
    assert len(calls)==3 and result['transport_repair_round']==2
