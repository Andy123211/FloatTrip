from datetime import date
import pytest
from app.planning.grounded import Requirements, deterministic_limits, same_entity, choose_poi, forbidden, hard_violations
from app.planning.schemas import TravelPlanState, OptimizerCandidate
from app.planning.optimizer import AttractionSubsetOptimizer


def test_alias_matching_and_whole_poi_resolution():
    assert same_entity('上海迪士尼乐园','上海迪士尼度假区','上海')
    assert same_entity('中山陵','中山陵景区','南京')
    assert not same_entity('豫园','豫园商城','上海')
    raw=[{'id':'gate','name':'示例博物馆-南门','location':'120,30'},{'id':'whole','name':'示例博物馆','location':'120,30','type':'科教文化服务;博物馆'}]
    spot,ev=choose_poi('示例博物馆',[],raw,'示例市')
    assert spot['id']=='whole' and ev['status']=='resolved'


def test_explicit_query_bounds_survive_soft_dialogue_projection():
    req=deterministic_limits(Requirements(),'节奏放缓，不要紧凑，每天最多2个景点，10:30以后开始，不去主题乐园')
    assert req.slow and req.max_per_day==2 and req.earliest_start=='10:30'
    assert 'themepark' in req.forbidden_tags
    assert forbidden({'poi_name':'示例乐园','semantic_tags':['themepark']},req,'示例市')


def test_entire_ledger_missing_must_cannot_pass_empty_candidate_pool():
    s=TravelPlanState(query='必须去示例馆',destination='示例市',hard_requirements=Requirements(must_names=['示例馆']).model_dump())
    assert hard_violations(s)==['MISSING_MUST:示例馆']


@pytest.mark.parametrize('budget',[0,1])
def test_earliest_and_directional_transport_respected_by_solver_and_fallback(budget):
    a=OptimizerCandidate(poi_name='A',location={'lng':120,'lat':30},duration_min=60,must_visit=True,earliest_start_min=630,transfer_minutes_to={'B':90},before_poi_names=['B'])
    b=OptimizerCandidate(poi_name='B',location={'lng':120.1,'lat':30},duration_min=60,must_visit=True,earliest_start_min=630)
    result=AttractionSubsetOptimizer(max_time_seconds=budget).solve([a,b],days=1,max_per_day=2,habit_preference='慢节奏',travel_start_date=date(2026,10,13))
    x,y=result.route[0]['spots'];assert x['name']=='A' and x['start_min']>=630
    assert y['start_min']-x['end_min']>=90
    assert result.quality_report.passed


def test_explicit_fixed_start_cannot_override_later_start_boundary():
    a=OptimizerCandidate(poi_name='A',location={'lng':120,'lat':30},duration_min=60,must_visit=True,earliest_start_min=630,fixed_start_min=540)
    from app.planning.optimizer import OptimizationFailure
    with pytest.raises(OptimizationFailure):AttractionSubsetOptimizer().solve([a],days=1,max_per_day=1)
