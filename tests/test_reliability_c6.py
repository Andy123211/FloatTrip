"""General regressions from archived failures; no provider or model calls."""
import asyncio
from datetime import date

import pytest

from app.planning import grounded
from app.planning.reliability import opening_calendar, opening_day
from app.planning.schemas import OptimizerCandidate, TravelPlanState
from app.planning.optimizer import AttractionSubsetOptimizer


def poi(identifier, name, **extra):
    return {'id':identifier, 'name':name, 'location':'120,30',
            'type':'风景名胜;公园广场;公园', **extra}


def test_exact_requested_name_beats_unverified_model_alias():
    rows = [poi('mall', '古园商城', type='购物服务;商场;购物中心'),
            poi('garden', '示例市古园')]
    spot, evidence = grounded.choose_poi('古园', ['古园商城'], rows, '示例市')
    assert spot and spot['id'] == 'garden'
    assert evidence['match_basis'] == 'primary_name_exact'
    assert evidence['alias_status'] == 'model_suggestions_not_verified'


def test_same_name_distinct_ids_stay_ambiguous_but_duplicate_response_does_not():
    a = poi('a', '示例馆', address='东路1号')
    b = poi('b', '示例馆', address='西路2号', location='120.1,30')
    assert grounded.choose_poi('示例馆', [], [a, b, a], '示例市')[0] is None
    spot, _ = grounded.choose_poi('示例馆', [], [a, a], '示例市')
    assert spot['id'] == 'a'


def test_provider_alias_evidence_can_resolve_must_without_trusting_model_alias():
    rows = [poi('museum', '某省历史文物陈列馆', alias='旧史馆|文物馆'),
            poi('mall', '旧史馆商城', type='购物服务;商场;购物中心')]
    spot, evidence = grounded.choose_poi('旧史馆', ['旧史馆商城'], rows, '示例市', allow_model_alias=False)
    assert spot['id'] == 'museum'
    assert evidence['match_basis'] == 'provider_alias_exact'
    assert evidence['provider_aliases'] == ['旧史馆','文物馆']
    assert evidence['provider_alias_source'] == 'amap:poi:museum'


def test_shared_provider_alias_is_not_proof_two_entities_are_one():
    rows = [poi('one', '第一陈列馆', alias='旧史馆'), poi('two', '第二陈列馆', alias='旧史馆')]
    spot, evidence = grounded.choose_poi('旧史馆', [], rows, '示例市', allow_model_alias=False)
    assert spot is None and evidence['status'] == 'ambiguous'


def test_must_lookup_does_not_accept_only_an_unverified_alias(monkeypatch):
    calls = []
    async def fake_search(city, key, **kwargs):
        calls.append(kwargs['keywords'])
        return [poi('mall', '古园商城', type='购物服务;商场;购物中心')]
    monkeypatch.setattr(grounded.helpers, 'search_attraction_pois_async', fake_search)
    monkeypatch.setattr('app.planning.nodes.amap_key', lambda: 'offline')
    spot, evidence = asyncio.run(grounded.lookup('示例市', '古园', ['古园商城'], must=True))
    assert spot is None and evidence['status'] == 'unresolved'
    assert calls == ['古园', '示例市 古园']


def test_model_alias_cannot_overwrite_explicit_requirement_query(monkeypatch):
    calls = []
    async def lookup(city, name, aliases=(), must=False):
        calls.append((name, aliases, must))
        return ({'id':name, 'name':name, 'location':{'lng':120,'lat':30}},
                {'query':name, 'name':name, 'status':'resolved'})
    monkeypatch.setattr(grounded, 'lookup', lookup)
    seed = {'name':'古园', 'aliases':['古园商城'], 'role':'must', 'reason':'explicit',
            'duration_min':120, 'duration_max':180, 'preference_match':1,
            'representativeness':.8, 'tags':['history'], 'draft_day':1}
    seeds = [seed] + [{**seed, 'name':f'其它点{i}', 'aliases':[], 'role':'classic'} for i in range(3)]
    state = TravelPlanState(query='必须去古园', destination='示例市', days=1,
        planning_variant='C', hard_requirements={'must_names':['古园']},
        planning_draft={'candidates':seeds})
    asyncio.run(grounded.search(state))
    assert calls[0] == ('古园', [], True)
    assert sum(name == '古园' for name, _, _ in calls) == 1


SEASONS = ('旺季4月1日至10月31日 周二至周日 08:30-17:00 16:00停止检票，'
           '淡季11月1日至3月31日 周二至周日 08:30-16:30 15:30停止检票，'
           '周一全天关闭，节假日营业时间以官方通知为准')


@pytest.mark.parametrize('visit,close,entry', [(date(2026,10,13),1020,960),
                                            (date(2026,11,3),990,930),
                                            (date(2027,1,5),990,930)])
def test_named_seasons_keep_explicit_dates_and_entry_limits(visit, close, entry):
    rule = opening_day(SEASONS, visit)
    assert rule['status'] == 'partial'
    assert rule['intervals'] == [[510,close]] and rule['last_entry'] == entry
    assert 'date_exception_requires_verification' in rule['pending']


def test_season_words_without_dates_and_conflicting_schedules_remain_unknown():
    assert opening_day('旺季08:30-17:00，淡季08:30-16:30', date(2026,10,13))['status'] == 'unknown'
    assert opening_day('旺季4月1日至10月31日09:00-17:00；淡季10月1日至12月31日09:00-18:00', date(2026,10,13))['status'] == 'unknown'
    monday = opening_day(SEASONS, date(2026,10,12))
    assert monday['intervals'] == []


@pytest.mark.parametrize('prefix', ['旺季：', '旺季 ', '旺季（'])
def test_season_caption_punctuation_keeps_its_date(prefix):
    raw = prefix + '4月1日至10月31日）09:00-17:00'
    assert opening_day(raw, date(2026,10,13))['intervals'] == [[540,1020]]


@pytest.mark.parametrize('solver_seconds', [0,1])
def test_season_calendar_is_shared_by_solver_and_final_delivery_check(solver_seconds):
    visit = date(2026,10,13)
    candidate = OptimizerCandidate(poi_name='示例历史景区', poi_id='history',
        location={'lng':120,'lat':30}, duration_min=240, must_visit=True,
        preferred_period='evening', opening_calendar=opening_calendar({'open_time':SEASONS},visit,1))
    result = AttractionSubsetOptimizer(max_time_seconds=solver_seconds).solve(
        [candidate], days=1, max_per_day=1, travel_start_date=visit)
    assert result.route[0]['spots'][0]['end_min'] <= 1020
    bad = [{'day':1,'spots':[{'name':candidate.poi_name,'start_time':'16:30','end_time':'20:30'}]}]
    state = TravelPlanState(query='游览', days=1, travel_start_date=visit,
        candidate_pool=[candidate.model_dump()], route=bad)
    assert 'OPENING_CONFLICT:示例历史景区' in grounded.validation(state)['hard_violations']
