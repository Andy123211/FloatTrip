import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from app.core import amap_budget, http
from app.core.amap_cache import AmapCache

URL = 'https://restapi.amap.com/v3/place/text?key=secret&keywords=example'

@pytest.fixture
def budget(tmp_path, monkeypatch):
    path = tmp_path/'budget.sqlite'
    monkeypatch.setenv('AMAP_BUDGET_DB',str(path))
    monkeypatch.setenv('AMAP_BUDGET_LIMIT','3')
    monkeypatch.delenv('AMAP_BUDGET_UNLIMITED', raising=False)
    monkeypatch.setenv('AMAP_CACHE_PATH',str(tmp_path/'cache.sqlite'))
    return path


def test_concurrent_reservation_persists_and_redacts(budget):
    def attempt(_):
        try: amap_budget.reserve(URL);return True
        except amap_budget.AmapBudgetExhausted:return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt,range(20))) == 3
    assert amap_budget.status()['used'] == 3
    with sqlite3.connect(budget) as db:
        assert all('secret' not in row[0] for row in db.execute('SELECT url FROM requests'))
    # New process observes the same ledger (server restart cannot reset usage).
    output = subprocess.check_output([sys.executable,'-c','from app.core.amap_budget import status; print(status()["used"])'])
    assert output.strip() == b'3'


def test_failed_network_attempts_count_and_next_attempt_is_blocked(budget,monkeypatch):
    calls=[]
    def fail(*a,**kw):calls.append(1);raise OSError('offline')
    monkeypatch.setattr(http.urllib.request,'urlopen',fail)
    monkeypatch.setattr(http.time,'sleep',lambda _:None)
    with pytest.raises(RuntimeError):http.http_get_json(URL)
    assert len(calls)==3
    with pytest.raises(amap_budget.AmapBudgetExhausted):http.http_get_json(URL)
    assert len(calls)==3


def test_async_attempts_and_cached_response_after_exhaustion(budget,monkeypatch):
    calls=[]
    class Client:
        async def get(self,*a,**kw):calls.append(1);raise OSError('offline')
    async def client():return Client()
    async def sleep(_):pass
    monkeypatch.setattr(http,'get_async_http_client',client)
    monkeypatch.setattr(http.asyncio,'sleep',sleep)
    with pytest.raises(RuntimeError):asyncio.run(http.http_get_json_async(URL))
    assert len(calls)==3
    AmapCache().put(URL,{'status':'1','pois':[{'id':'cached'}]})
    assert asyncio.run(http.http_get_json_async(URL))['pois'][0]['id']=='cached'
    assert amap_budget.status()['used']==3 and amap_budget.status()['cache_hits']==1


def test_expired_cache_is_not_relabelled_fresh(budget,monkeypatch):
    AmapCache().put(URL,{'status':'1','pois':[{'id':'old'}]},observed_at=1)
    assert AmapCache().get(URL) is None


def test_frozen_c5_calendar_and_historical_alias_regression():
    root=Path(__file__).resolve().parents[1]
    out=root/'artifacts/interview-demo/shanghai-c5-match-v1'
    code='''
import json, hashlib
from pathlib import Path
from app.planning.grounded import choose_poi
root=Path(ROOT)
frozen=root/'artifacts/travel-benchmark/c5-online-v1/runtimes/C5/app/planning/reliability.py'
assert Path('app/planning/reliability.py').read_bytes()==frozen.read_bytes()
record=json.loads((root/'artifacts/travel-benchmark/c5-online-v1/runs/C5/trials/sh-must-avoid.planning.3.json').read_text())
rows=[p for call in record['search'] for p in call.get('pois',[])]
spot,ev=choose_poi('豫园',['Yu Garden','豫园商城'],rows,'上海',allow_model_alias=False)
assert spot and spot['id']=='B00155MF55', (spot,ev)
print(json.dumps({'source':'preserved_failed_online_response','resolved_name':spot['name'],'id':spot['id'],'basis':ev['match_basis']},ensure_ascii=False))
'''.replace('ROOT',repr(str(root)))
    result=subprocess.run([sys.executable,'-c',code],cwd=out,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    print(result.stdout)


def test_absolute_cap_cannot_be_raised_by_phase_environment(budget,monkeypatch):
    monkeypatch.setenv('AMAP_BUDGET_LIMIT','9999')
    for _ in range(50):amap_budget.reserve(URL)
    with pytest.raises(amap_budget.AmapBudgetExhausted):amap_budget.reserve(URL)
    assert amap_budget.status()['used']==50


def test_explicit_unlimited_keeps_count_and_allows_requests_past_fifty(budget,monkeypatch):
    monkeypatch.setenv('AMAP_BUDGET_UNLIMITED','1')
    for _ in range(52):amap_budget.reserve(URL)
    report=amap_budget.status()
    assert report['used']==52 and report['unlimited'] is True
    assert report['limit'] is None and report['remaining'] is None and report['phase_limit'] is None
    output=subprocess.check_output([sys.executable,'-c','from app.core.amap_budget import status; import json; print(json.dumps(status()))'])
    assert json.loads(output)['used']==52
    # Re-enabling an explicit bound must retain usage rather than reset it.
    monkeypatch.setenv('AMAP_BUDGET_UNLIMITED','0')
    with pytest.raises(amap_budget.AmapBudgetExhausted):amap_budget.reserve(URL)


def test_frozen_finalizer_rejects_failed_validation_before_persisting():
    root=Path(__file__).resolve().parents[1]
    out=root/'artifacts/interview-demo/shanghai-c5-match-v1'
    code='''
import asyncio
from app.planning.runtime_worker import PlanningFinalizer
async def check():
 f=PlanningFinalizer(None,None)
 def persist(*a):raise AssertionError('Must never persist a failed itinerary')
 f._persist=persist
 try:
  await f({'request_snapshot':{'query':'测试','destination':'上海','days':1}},
          {'final_plan':{'days':[{'day':1}], 'validation_summary':{'status':'failed','hard_violations':['MISSING_MUST']}}},'')
 except RuntimeError as exc:assert '未通过' in str(exc)
 else:raise AssertionError('Expected refusal')
asyncio.run(check())
'''
    r=subprocess.run([sys.executable,'-c',code],cwd=out,text=True,capture_output=True)
    assert r.returncode==0,r.stdout+r.stderr
