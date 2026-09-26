from copy import deepcopy

from .common import DATA, read
from .evidence import Catalog, evaluate, quality_verdict
from .report import summarize


def fixture():
    case=deepcopy(next(c for c in read(DATA/'cases.json')['cases'] if c['id']=='sh-must-avoid'))
    case['input']['days']=1
    names=['外滩','上海豫园']
    spots=[{'name':n,'start_time':f'{10+i*3}:00','end_time':f'{12+i*3}:00'} for i,n in enumerate(names)]
    record={'status':'succeeded','state':{'pois':[{'name':n} for n in names],
        'route':[{'day':1,'spots':spots}], 'final_plan':{'days':[{'day':1,'timeline':[{'type':'attraction',**s} for s in spots]}]}}}
    return case,record


def test_alias_good_route_and_no_reference_leak():
    case,r=fixture()
    ev=evaluate(r,case)
    assert ev['hard_violations']==[]
    assert len(ev['must_visit_satisfied'])==2
    assert 'eligible_hot' not in case['input']


def test_duplicate_parent_child_and_gate():
    cat=Catalog()
    assert cat.match('上海豫园','上海')['id']=='sh-yuyuan'
    assert cat.match('外滩-观景平台','上海')['kind']=='subpoint'
    assert cat.match('外滩饭店','上海')['kind']=='unknown'
    assert cat.group('nj-sun')==cat.group('nj-ming')
    case,r=fixture()
    timeline=r['state']['final_plan']['days'][0]['timeline']
    timeline.append(deepcopy(timeline[0]))
    ev=evaluate(r,case)
    assert any(x.startswith('DUPLICATE') for x in ev['hard_violations'])
    assert ev['coverage']['selected']['count']==2


def test_avoid_alias_time_conflict_and_missing_must():
    case,r=fixture()
    r['state']['final_plan']['days'][0]['timeline'][1].update(name='迪士尼',start_time='11:00')
    ev=evaluate(r,case)
    assert 'AVOID:迪士尼' in ev['hard_violations']
    assert any(x.startswith('TIME_OVERLAP') for x in ev['hard_violations'])
    assert 'MISSING_MUST:豫园' in ev['hard_violations']


def test_unknown_not_cold_and_missing_time_not_passed():
    case,r=fixture()
    r['state']['final_plan']['days'][0]['timeline'][0].update(name='未核实的公园',start_time=None)
    ev=evaluate(r,case)
    assert '未核实的公园' in ev['unknown_pois']
    assert ev['facts'][0]['tier']=='unverified'
    assert '时间缺失或格式无法核验' in ev['facts'][0]['notes']


def test_opening_and_explicit_constraints():
    case,r=fixture()
    case['expectations'].update(earliest_start='10:30',max_per_day=1)
    r['state']['final_plan']['days'][0]['timeline'][1].update(name='上海博物馆',start_time='18:00',end_time='20:00')
    ev=evaluate(r,case)
    assert 'KNOWN_OPENING_CONFLICT:上海博物馆' in ev['hard_violations']
    assert any(x.startswith('EARLY_START') for x in ev['hard_violations'])
    assert any(x.startswith('DAILY_MAX') for x in ev['hard_violations'])


def test_failed_trials_kept_in_denominator_and_hard_gate():
    review={'scores':dict(popularity=5,personalization=5,route=5,validity=5)}
    good=quality_verdict(review,{'hard_violations':[]},'succeeded')
    bad=quality_verdict(review,{'hard_violations':['AVOID:x']},'succeeded')
    rows=[dict(status='succeeded',**good),dict(status='failed',**quality_verdict(None,{'hard_violations':[]},'failed'))]
    assert summarize(rows)['quality_pass_rate']==.5
    assert not bad['passed']


def test_catalog_cases_consistent_and_counts():
    cat=Catalog()
    data=read(DATA/'cases.json')['cases']
    assert len(data)==24
    assert sum(c['dialogue'] for c in data)==4
    for c in data:
        assert all(id in cat.by_id for key in ['eligible_hot','must_visit','avoid'] for id in c['expectations'][key])
        assert c['expectations']['hot_group_target']<=len({cat.group(id) for id in c['expectations']['eligible_hot']})


def test_source_backed_alias_adjudication_is_exact_not_internal_point():
    cat=Catalog(adjudications={'aliases':[{'id':'nj-keju','name':'中国科举博物馆(江南贡院)','kind':'exact','url':'https://www.njiemuseum.com/'}]})
    assert cat.match('中国科举博物馆(江南贡院)','南京')=={'id':'nj-keju','kind':'exact'}
    assert cat.match('中国科举博物馆-西门','南京')['kind']=='subpoint'


def test_unknown_pois_not_grouped_and_parent_avoid_not_expanded():
    case,r=fixture()
    case['city']='南京'
    case['expectations'].update(avoid=['nj-qinhuai'],must_visit=[])
    names=['南京中国科举博物馆','老门东']
    for spot,name in zip(r['state']['final_plan']['days'][0]['timeline'],names):
        spot['name']=name
    ev=evaluate(r,case)
    assert not any(x.startswith('AVOID') for x in ev['hard_violations'])
    for spot,name in zip(r['state']['final_plan']['days'][0]['timeline'],['unknown one','unknown two']):
        spot['name']=name
    ev=evaluate(r,case)
    assert not any('同景区' in note for f in ev['facts'] for note in f['notes'])


def test_source_drift_detected_before_and_after_trial(tmp_path, monkeypatch):
    import hashlib
    from . import common
    monkeypatch.setattr(common, 'ROOT', tmp_path)
    source = tmp_path / 'example.py'
    source.write_text('original')
    manifest = {'source_hashes': {'example.py': hashlib.sha256(source.read_bytes()).hexdigest()}}
    assert common.source_changes(manifest) == []
    source.write_text('changed')
    assert common.source_changes(manifest)[0]['path'] == 'example.py'
    source.unlink()
    assert common.source_changes(manifest)[0]['actual'] is None
