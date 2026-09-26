from pathlib import Path
from collections import Counter
import hashlib
from .common import DATA, ROOT, read
from .evidence import Catalog
from .experiment import interval


def test_four_city_frozen_cases_preserve_original_and_holdout_boundaries():
    data=DATA/'four_city_v2';cases=read(data/'cases.json')['cases'];cat=Catalog(read(data/'catalog.json'))
    assert len(cases)==48 and Counter(c['city'] for c in cases)==dict.fromkeys(['上海','南京','北京','重庆'],12)
    assert cases[:24]==read(DATA/'cases.json')['cases']
    assert sum(c['dialogue'] for c in cases)==8
    for c in cases:
        assert c['input']['destination']==c['city']
        assert c['input']['start_date']=='2026-10-13'
        for key in ['eligible_hot','must_visit','avoid']:
            assert all(cat.by_id[i]['city']==c['city'] for i in c['expectations'][key])
        assert not any(k in c['input'] for k in ['eligible_hot','expectations','catalog'])
    frozen=read(data/'freeze.json')
    for name,value in frozen['hashes'].items():assert hashlib.sha256((data/name).read_bytes()).hexdigest()==value


def test_production_grounded_module_does_not_import_benchmark_or_hardcode_cities():
    body=(ROOT/'app/planning/grounded.py').read_text()
    for forbidden in ['tests.travel_benchmark','four_city_v2','上海','南京','北京','重庆']:
        assert forbidden not in body


def test_baseline_snapshot_retains_preimplementation_files():
    out=ROOT/'artifacts/travel-benchmark/four-city-v2'
    if not (out/'baseline_manifest.json').exists():return
    for name,value in read(out/'baseline_manifest.json')['source_hashes'].items():
        assert hashlib.sha256((out/'baseline'/name).read_bytes()).hexdigest()==value
    assert not (out/'baseline/app/planning/grounded.py').exists()


def test_case_cluster_interval_is_deterministic():
    assert interval([2,2,2])==[2,2]
    assert interval([1,2,3])==interval([1,2,3])


def test_anonymous_packet_preserves_failed_null_plan(tmp_path):
    from .common import write
    from .experiment import packets
    case=read(DATA/'four_city_v2/cases.json')['cases'][0]
    write(tmp_path/'frozen/cases.json',{'cases':[case]})
    write(tmp_path/'schedule.json',[{'case_id':case['id'],'city':case['city'],'mode':'planning','trial':1,'variant':'C'}])
    key=f"{case['id']}.planning.1.json"
    write(tmp_path/'runs/C/trials'/key,{'status':'failed','state':{'final_plan':None},'error':{'type':'ValidationError'}})
    write(tmp_path/'runs/C/evidence'/key,{'hard_violations':[],'stage_names':['private'],'missed_hot':[]})
    packets(tmp_path)
    packet=read(tmp_path/'blind_packets/review-001.json')
    assert packet['status']=='failed' and packet['failure_type']=='ValidationError'
    assert packet['planning_notes']==[] and 'variant' not in packet
    assert 'stage_names' not in packet['evidence']


def test_comparison_requires_complete_sealed_reviews(tmp_path):
    import pytest
    from .experiment import compare,freeze_reviews
    with pytest.raises(SystemExit,match='freeze-reviews'):compare(tmp_path)
    with pytest.raises(SystemExit,match='456'):freeze_reviews(tmp_path)


def test_outage_sensitivity_excludes_all_arms_of_matched_block():
    from .experiment import service_sensitivity
    schedule=[];rows=[]
    for repeat in [1,2]:
        for arm,score in [('A',50),('B',60),('C',80)]:
            row=dict(case_id='sample',city='上海',mode='planning',trial=repeat,variant=arm)
            schedule.append(row);rows.append(dict(row,score=score if repeat==2 else 0,passed=score>=80 and repeat==2))
    result=service_sensitivity(rows,schedule,{'affected_schedule_range':[2,2]})
    assert result['excluded_matched_blocks']==1 and result['excluded_rows']==3
    comparison=next(p for p in result['paired'] if p['city']=='上海' and p['comparison']=='C-A')
    assert comparison['score_delta']==30 and comparison['paired_cases']==1 and comparison['paired_repeats']==1
    assert len(rows)==6  # The primary denominator is untouched.


def test_new_experiment_freezes_shared_cache_overlay(tmp_path):
    from .experiment import initialize_cache_policy
    initialize_cache_policy(tmp_path)
    policy=read(tmp_path/'cache_policy_v1/policy.json')
    assert policy['first_schedule_index']==1 and policy['completed_before_change']==0
    assert len(policy['code_hashes'])==2
    for name,expected in policy['code_hashes'].items():
        assert hashlib.sha256((tmp_path/'cache_policy_v1'/name).read_bytes()).hexdigest()==expected
