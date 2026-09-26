import asyncio
import json
from unittest.mock import AsyncMock, Mock
import pytest
from app.core import amap_cache as cache

U = 'https://restapi.amap.com/v3/place/text?key=secret&city=上海&keywords=外滩&page=1&types=110000&offset=25'
BODY = {'status':'1','pois':[{'id':'one','name':'外滩'}]}


def test_full_key_and_secret_removal():
    assert cache.identity(U) == cache.identity(U.replace('key=secret','key=other'))
    for before, after in [('page=1','page=2'),('types=110000','types=050000'),('offset=25','offset=10'),('city=上海','city=南京')]:
        assert cache.identity(U)[0] != cache.identity(U.replace(before,after))[0]
    assert 'secret' not in cache.identity(U)[1]
    assert cache.identity(U.replace('restapi.amap.com','other.com')) is None


def test_persistent_expiry_error_and_empty(tmp_path):
    clock = [1000.]
    db = tmp_path/'cache.sqlite'
    c = cache.AmapCache(db,clock=lambda:clock[0])
    assert c.put(U, {'status':'0','info':'quota'}) is None
    assert c.put(U, {'status':'1'}) is None
    assert c.get(U) is None
    c.put(U,BODY,source='fixture')
    assert cache.AmapCache(db,clock=lambda:1001).get(U)[0] == BODY
    assert c.get(U)[1]['source']=='fixture'
    clock[0]+=604801
    assert c.get(U) is None
    c.put(U,{'status':'1','pois':[]})
    clock[0]+=901
    assert c.get(U) is None
    assert c.put(U,BODY,observed_at=1000) is None
    assert c.put(U,BODY,observed_at=clock[0]+100) is None
    assert b'secret' not in db.read_bytes()


def test_route_direction_and_ttl(tmp_path):
    clock=[1000.];c=cache.AmapCache(tmp_path/'c',clock=lambda:clock[0])
    u='https://restapi.amap.com/v3/direction/transit/integrated?origin=1,2&destination=3,4&city=上海'
    c.put(u,{'status':'1','route':{'transits':[]}})
    assert c.get(u.replace('origin=1,2&destination=3,4','origin=3,4&destination=1,2')) is None
    clock[0]+=901;assert c.get(u) is None


def test_concurrent_async_then_sync_reuse(tmp_path,monkeypatch):
    monkeypatch.setenv('AMAP_CACHE_PATH',str(tmp_path/'c'))
    calls=[]
    async def fetch():
        calls.append(1);await asyncio.sleep(.01);return BODY
    async def run():
        return await asyncio.gather(*(cache.cached_json_async(U,fetch) for _ in range(8)))
    assert asyncio.run(run()) == [BODY]*8
    assert len(calls)==1
    network=Mock(side_effect=AssertionError('Network should not run'))
    assert cache.cached_json(U,network)==BODY
    network.assert_not_called()


def test_failures_are_retried_and_disable(tmp_path,monkeypatch):
    monkeypatch.setenv('AMAP_CACHE_PATH',str(tmp_path/'c'))
    bad=Mock(return_value={'status':'0','info':'CUQPS_HAS_EXCEEDED_THE_LIMIT'})
    cache.cached_json(U,bad);cache.cached_json(U,bad)
    assert bad.call_count==2
    monkeypatch.setenv('AMAP_CACHE_ENABLED','0')
    good=Mock(return_value=BODY)
    cache.cached_json(U,good);cache.cached_json(U,good)
    assert good.call_count==2


def test_http_helpers_share_cache(tmp_path,monkeypatch):
    from app.core import http
    monkeypatch.setenv('AMAP_CACHE_PATH',str(tmp_path/'c'))
    a=AsyncMock(return_value=BODY);s=Mock(side_effect=AssertionError('no network'))
    monkeypatch.setattr(http,'_http_get_json_async_uncached',a)
    monkeypatch.setattr(http,'_http_get_json_uncached',s)
    assert asyncio.run(http.http_get_json_async(U))==BODY
    assert http.http_get_json(U)==BODY
    assert a.await_count==1;s.assert_not_called()


def test_redis_key_parameters():
    from app.core.cache import poi_cache_key
    a=poi_cache_key('上海','景点',types='110000')
    assert a!=poi_cache_key('上海','景点',types='050000')
    assert a!=poi_cache_key('上海','景点',offset=10)
    assert a!=poi_cache_key('上海','景点',page=2)


def test_seed_cached_record_does_not_extend_expiry(tmp_path):
    from scripts.seed_amap_cache import seed
    c=cache.AmapCache(tmp_path/'c',clock=lambda:1000000.)
    p=tmp_path/'record.json'
    p.write_text(json.dumps({'started_at':'1970-01-12T13:46:40+00:00','api_calls':[{'url':U,'status_code':200,'response':BODY,'cache_hit':True}],'amap_request_cache':[{'observed_at':1000.}]}))
    assert seed([p],c).get('eligible_success_records',0)==0
    assert c.get(U) is None
