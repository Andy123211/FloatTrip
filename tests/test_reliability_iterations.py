from datetime import date
import pytest

from app.planning.reliability import opening_day, opening_calendar, available_starts, is_facility
from app.planning.optimizer import AttractionSubsetOptimizer
from app.planning.schemas import OptimizerCandidate


def test_regular_hours_survive_holiday_qualification_without_becoming_verified():
    rule = opening_day('周二至周日09:00-17:30(17:00停止入馆),周一闭馆（法定节假日除外）', date(2026, 10, 13))
    assert rule['status'] == 'partial'
    assert rule['intervals'] == [[540, 1050]] and rule['last_entry'] == 1020
    cal = {'days': {'2026-10-13': rule}}
    assert available_starts({'duration_min': 120, 'opening_calendar': cal}, date(2026, 10, 13), 540, 1260) == [(540, 930)]


def test_weekday_closure_and_month_rules_do_not_leak_across_clauses():
    raw = '周三至周四,周六至周日09:00-17:00 最晚进入16:00 周一至周二,周五全天不开放；国定假另行通知'
    assert opening_day(raw, date(2026, 10, 13))['intervals'] == []
    assert opening_day(raw, date(2026, 10, 14))['intervals'] == [[540, 1020]]
    raw = '1月,12月07:00-17:30；2月至11月06:30-18:30'
    assert opening_day(raw, date(2026, 10, 13))['intervals'] == [[390, 1110]]
    assert opening_day(raw, date(2026, 12, 13))['intervals'] == [[420, 1050]]


def test_unknown_season_and_conflicting_rules_are_not_invented():
    assert opening_day('冬令时09:00-17:00；夏令时09:00-19:00', date(2026, 10, 13))['status'] == 'unknown'
    assert opening_day('周一闭馆，节假日除外', date(2026, 10, 13))['status'] == 'unknown'
    assert opening_day('09:00-17:00；10:00-18:00', date(2026, 10, 13))['status'] == 'unknown'


def test_numbered_gate_is_not_an_independent_destination():
    assert is_facility('示例公园(3口)')
    assert is_facility('示例公园（2号门）')
    assert not is_facility('示例博物馆(东馆)')


@pytest.mark.parametrize('period', ['any', 'morning', 'afternoon', 'evening'])
def test_consistent_objective_above_daily_target_and_period_flags(period):
    pool = [OptimizerCandidate(poi_name=f'点{i}', poi_id=str(i), location={'lng':120+i*.001,'lat':30},
            duration_min=45, must_visit=True, preferred_period=period) for i in range(5)]
    result = AttractionSubsetOptimizer(consistent_objective=True).solve(pool, days=1, max_per_day=5, user_daily_max=5)
    assert len(result.route[0]['spots']) == 5
    assert result.quality_report.passed
    assert result.quality_report.solver_objective_delta <= 1e-6


@pytest.mark.parametrize('budget', [0, 1])
def test_final_edge_graph_allows_reordering_and_optional_deletion(budget):
    pool = [OptimizerCandidate(poi_name=name, poi_id=name, location={'lng':120,'lat':30},
            duration_min=60, must_visit=name=='A', preference_match=1 if name=='A' else .5,
            allowed_transfer_names=['A'] if name=='B' else [],
            transfer_minutes_to={'A':50} if name=='B' else {}) for name in ['A','B','C']]
    result = AttractionSubsetOptimizer(consistent_objective=True, max_time_seconds=budget).solve(pool, days=1, max_per_day=2)
    names = [s['name'] for s in result.route[0]['spots']]
    assert names == ['B','A']
    assert result.quality_report.passed


def test_last_repair_never_introduces_unobserved_edges(monkeypatch):
    import asyncio
    from app.planning import grounded, nodes
    from app.planning.schemas import TravelPlanState
    calls = []
    pool = [OptimizerCandidate(poi_name=n, poi_id=n, location={'lng':120,'lat':30}, duration_min=60,
                               must_visit=n=='A').model_dump() for n in ['A','B','C']]
    async def solve(state):
        calls.append(state)
        names = ['A','B'] if len(calls)==1 else ['A','C']
        if len(calls)==3:
            by_name = {c['poi_name']: c for c in state.candidate_pool}
            assert by_name['A']['allowed_transfer_names'] == ['B','C']
            assert by_name['B']['allowed_transfer_names'] == []
            assert by_name['C']['allowed_transfer_names'] == []
            assert not by_name['C']['must_visit'] and not by_name['C']['fixed_day']
        return {'route':[{'day':1,'spots':[{'name':n,'start_time':f'{10+i*2}:00','end_time':f'{11+i*2}:00'} for i,n in enumerate(names)]}], 'quality_report':{'passed':True}}
    async def estimate(city, left, right, mode):
        return {'from':left['name'],'to':right['name'],'mode':mode,'status':'estimated','duration_minutes':30,'buffer_minutes':15}
    monkeypatch.setattr(nodes,'optimize_attractions_node',solve)
    monkeypatch.setattr(grounded,'transport_estimate',estimate)
    result = asyncio.run(grounded.optimize(TravelPlanState(query='去A',days=1,planning_variant='C',candidate_pool=pool,hard_requirements={'must_names':['A']})))
    assert len(calls)==3 and result['transport_repair_round']==2
    assert result['transport_repair_trace'][-1]['edge_policy']=='observed_only'


def test_unknown_venue_early_preference_does_not_invent_hours_or_override_user():
    from app.planning.grounded import evidence_aware_periods
    from app.planning.schemas import TravelPlanState
    c = OptimizerCandidate(poi_name='某历史馆', location={'lng':120,'lat':30}, category='museum',
        preferred_period='afternoon', opening_calendar=opening_calendar({}, date(2026,10,13),1)).model_dump()
    state = TravelPlanState(query='历史主题游', days=1)
    updated = evidence_aware_periods([c], state)[0]
    assert updated['preferred_period']=='morning'
    assert updated['opening_calendar']==c['opening_calendar']
    assert updated['opening_calendar']['days']['2026-10-13']['status']=='unknown'
    assert evidence_aware_periods([c], state.model_copy(update={'query':'下午参观历史馆'}))[0]['preferred_period']=='afternoon'
    assert evidence_aware_periods([c], state.model_copy(update={'hard_requirements':{'earliest_start':'13:00'}}))[0]['preferred_period']=='afternoon'
    fixed = {**c, 'fixed_start_min': 840}
    assert evidence_aware_periods([fixed], state)[0]['preferred_period']=='afternoon'
