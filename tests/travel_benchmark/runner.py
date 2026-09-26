"""Read-only production graph instrumentation, isolated to each trial subprocess."""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from .common import clean, digest, now, read, source_changes, write


class Capture:
    def __init__(self, path, record):
        self.path, self.record = Path(path), record

    def save(self):
        write(self.path, self.record)

    @contextmanager
    def instrument(self):
        import app.planning.graph as graph
        import app.planning.helpers as helpers
        import app.planning.nodes as nodes
        import app.providers.amap.poi as poi
        import app.core.http as http
        original_wrapper = graph._with_progress
        original_search = helpers.search_attraction_pois_async
        original_merged = nodes.fetch_city_spots_async
        original_cache = poi.get_cached
        original_llm = nodes.build_structured_llm
        import httpx
        from langchain_core.language_models.chat_models import BaseChatModel
        original_request = httpx.AsyncClient.request
        original_agenerate = BaseChatModel.agenerate
        namespace = os.getenv('BENCHMARK_CACHE_NAMESPACE')
        # A and improved arms get the same per-trial cold-cache policy. Prefixing
        # reads and writes prevents access to the user's production cache.
        import app.core.cache as core_cache
        original_get, original_set = core_cache.get_cached, core_cache.set_cached
        def isolated_get(key):
            return original_get(f'{namespace}:{key}' if namespace else key)
        def isolated_set(key, value, ttl):
            return original_set(f'{namespace}:{key}' if namespace else key, value, ttl)
        async def request(client, method, url, *args, **kwargs):
            started = time.monotonic()
            response = await original_request(client, method, url, *args, **kwargs)
            if 'restapi.amap.com' in str(url):
                self.record.setdefault('api_calls', []).append({'url':http.redact_url(str(url)), 'seconds':round(time.monotonic()-started,3), 'status_code':response.status_code, 'response':clean(response.json())})
            return response
        async def agenerate(model, *args, **kwargs):
            result = await original_agenerate(model, *args, **kwargs)
            self.record.setdefault('model_usage', []).append({'model':getattr(model,'model_name',None), 'llm_output':clean(result.llm_output), 'usage_metadata':[clean(getattr(g.message,'usage_metadata',None)) for row in result.generations for g in row]})
            return result
        def wrap(name, action):
            async def observed(state):
                item = {'name':name, 'started_at':now()}
                self.record['stages'].append(item)
                self.record['state'] = state.model_dump(mode='json')
                self.save()
                started = time.monotonic()
                try:
                    result = action(state)
                    if inspect.isawaitable(result):
                        result = await result
                    item['output'] = clean(result)
                    self.record['state'].update(clean(result))
                    if name=='attraction_search' and self.record['state'].get('planning_variant') in ('B','C'):
                        merged=self.record.get('merged_search', []) + result.get('pois', [])
                        self.record['merged_search']=list({p.get('id') or p['name']:p for p in merged}.values())
                    return result
                except BaseException as exc:
                    item['error']={'type':type(exc).__name__,'message':str(exc)}
                    raise
                finally:
                    item['seconds']=round(time.monotonic()-started,3)
                    self.save()
            return original_wrapper(name, observed)
        async def search(city, api_key, **kwargs):
            item={'city':city,'parameters':kwargs,'started_at':now()}
            self.record['search'].append(item)
            try:
                result=await original_search(city,api_key,**kwargs)
                item.update(pois=clean(result), response_hash=digest(result))
                return result
            finally:
                self.save()
        async def merged(*args,**kwargs):
            result=await original_merged(*args,**kwargs)
            self.record['merged_search']=clean(result)
            self.save()
            return result
        def cache(key):
            result=isolated_get(key)
            self.record['cache_reads'].append({'key':key,'hit':result is not None})
            return result
        def llm(*args,**kwargs):
            from app.llm.deepseek import resolve_deepseek_model
            self.record['model_calls_config'].append({'schema':getattr(args[0],'__name__','') if args else '',
                'provider':kwargs.get('provider'), 'model':resolve_deepseek_model(kwargs.get('model')),
                'temperature':kwargs.get('temperature')})
            return original_llm(*args,**kwargs)
        with ExitStack() as stack:
            stack.enter_context(patch.object(graph,'_with_progress',wrap))
            stack.enter_context(patch.object(helpers,'search_attraction_pois_async',search))
            stack.enter_context(patch.object(nodes,'fetch_city_spots_async',merged))
            stack.enter_context(patch.object(poi,'get_cached',cache))
            stack.enter_context(patch.object(poi,'set_cached',isolated_set))
            stack.enter_context(patch.object(httpx.AsyncClient,'request',request))
            stack.enter_context(patch.object(BaseChatModel,'agenerate',agenerate))
            import app.providers.weather.amap as weather
            if hasattr(weather,'get_cached'):
                stack.enter_context(patch.object(weather,'get_cached',isolated_get))
                stack.enter_context(patch.object(weather,'set_cached',isolated_set))
            stack.enter_context(patch.object(nodes,'build_structured_llm',llm))
            yield


async def planning(case, capture):
    from app.planning.graph import build_graph
    from app.planning.runtime_worker import snapshot_to_state
    snapshot=dict(case['input'])
    # Only public input fields, never catalog/expectations.
    state=snapshot_to_state(snapshot)
    capture.record['submitted_snapshot']=snapshot
    result=await build_graph(memory_writer=None).ainvoke(state,config={'recursion_limit':30})
    capture.record['state']=clean(result)


async def dialogue(case, capture):
    from app.core.database import configure_database, init_db, get_conn
    from app.core.travel_memory import MemoryRepository
    from app.runtime.manager import RunManager
    from app.runtime.models import RunStatus
    from app.runtime.repositories import ConversationRepository
    from app.chat.service import ChatService
    from app.chat.react_graph import build_main_agent_graph
    from app.chat.tool_service import MainAgentToolService
    from app.chat.tools import build_main_agent_tools
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    import app.planning.runtime_worker as runtime
    db=capture.path.parent/(capture.path.stem+'.sqlite')
    configure_database(db)
    init_db(db)
    with get_conn(db) as conn:
        conn.execute('INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)',('benchmark','benchmark','not-a-login',now()))
    memory=MemoryRepository(db)
    for fact in case['input'].get('memory_profile_snapshot',[]):
        memory.create('benchmark',**fact,status='active',source_kind='manual')
    manager=RunManager(db)
    service=ChatService(manager,db)
    conversation=ConversationRepository(db).create('benchmark',case['city'])
    capture.record['dialogue_turns']=[]
    # Post-itinerary enrichment and shadow analysis are outside the stated scope.
    # The real planning graph and real persistence finalizer remain intact.
    with patch.object(runtime,'_launch_silent_tip_task',lambda *a,**kw:None), patch.object(runtime,'launch_shadow_profiles',lambda *a,**kw:None):
        agent=build_main_agent_graph(build_main_agent_tools(MainAgentToolService(service,db)),checkpointer=InMemorySaver())
        messages=[case['input']['query']+' 请整理需求并生成完整行程。',
                  '确认，请按我上面给出的日期和全部要求生成完整行程，未指定的信息按合理默认处理。']
        for message in messages:
            _,run=await service.submit_message('benchmark',conversation['id'],message)
            await manager.transition(run['id'],RunStatus.RUNNING)
            inputs=await service.react_chat_input(run)
            context=await service.main_agent_context(run)
            config={'configurable':{'thread_id':run['id']},'recursion_limit':30}
            result=await agent.ainvoke(inputs,config=config,context=context)
            if result.get('__interrupt__'):
                capture.record['dialogue_turns'].append({'user':message,'interrupt':clean(result['__interrupt__'])})
                result=await agent.ainvoke(Command(resume=case['input']['query']),config=config,context=context)
            capture.record['dialogue_turns'].append({'user':message,'messages':[clean(m.model_dump()) for m in result.get('messages',[])],
                'interrupt':clean(result.get('__interrupt__'))})
            await service.finalize_react_chat(run,result,'')
            await manager.transition(run['id'],RunStatus.SUCCEEDED)
            brief=service.briefs.latest_for_conversation('benchmark',conversation['id'])
            capture.record['brief']=clean(brief)
            capture.save()
            if capture.record['state'].get('final_plan'):
                capture.record['submitted_snapshot']=(brief or {}).get('submission_snapshot')
                break
        if not capture.record['state'].get('final_plan'):
            raise RuntimeError('Dialogue did not produce an itinerary after two scripted turns; inspect transcript')


async def execute(case, path, trial, mode, manifest):
    from app.core.env import load_local_env
    load_local_env()
    record={'case_id':case['id'],'trial':trial,'mode':mode,'started_at':now(),'status':'running',
            'source_fingerprint':manifest['source_fingerprint'],'benchmark_hash':manifest['benchmark_hash'],
            'stages':[],'search':[],'merged_search':[],'cache_reads':[],'model_calls_config':[],'state':{}}
    capture=Capture(path,record)
    capture.save()
    started=time.monotonic()
    try:
        frozen = Path(path).parent.parent / 'frozen'
        if digest({p.name:read(p) for p in sorted(frozen.glob('*.json'))}) != manifest['benchmark_hash']:
            raise RuntimeError('Frozen benchmark data changed before trial')
        record['source_changes_before'] = source_changes(manifest)
        if record['source_changes_before']:
            raise RuntimeError('Frozen source changed before trial; use a new experiment directory')
        with capture.instrument():
            await (dialogue(case,capture) if mode=='dialogue' else planning(case,capture))
        if not record['state'].get('final_plan'):
            raise RuntimeError('Production graph returned no final itinerary')
        record['status']='succeeded'
    except Exception as exc:
        record['status']='failed'
        record['error']={'type':type(exc).__name__,'message':str(exc)}
    finally:
        record['source_changes_after'] = source_changes(manifest)
        if record['source_changes_after']:
            record['status'] = 'failed'
            record['error'] = {'type': 'SourceChanged', 'message': 'Frozen source changed; result excluded from valid baseline, retained in denominator'}
        record['elapsed_seconds']=round(time.monotonic()-started,3)
        record['finished_at']=now()
        capture.save()
        from app.core.http import close_async_http_client
        await close_async_http_client()
    return record
