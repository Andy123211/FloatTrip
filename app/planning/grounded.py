"""General-purpose grounded planning variants; no benchmark or city-specific data."""
from __future__ import annotations
import asyncio
import json
import math
import os
import re
from difflib import SequenceMatcher
from urllib.parse import urlencode
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from langchain_core.exceptions import OutputParserException
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from app.planning.reliability import entity_id, is_facility, merge_candidates, opening_calendar
from app.planning import helpers
from app.planning.schemas import CandidateAttraction, OptimizerCandidate
from app.planning.semantics import link_candidate_to_poi
from app.planning.candidate_builder import candidate_pool_fingerprint
from app.providers.amap.poi import poi_to_spot
from app.core.http import http_get_json_async

class Requirements(BaseModel):
    model_config = ConfigDict(extra='forbid')
    must_names: list[str] = Field(default_factory=list)
    avoid_names: list[str] = Field(default_factory=list)
    forbidden_tags: list[str] = Field(default_factory=list, description='themepark, climbing or high_fatigue only when explicitly forbidden')
    earliest_start: str | None = None
    max_per_day: int | None = Field(default=None, ge=1, le=10)
    transport: Literal['transit','walking','driving'] = 'transit'
    slow: bool = False
    evidence: list[str] = Field(default_factory=list)

class Seed(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: Literal['must','classic','interest','alternative']
    reason: str
    duration_min: int = Field(ge=30, le=720)
    duration_max: int = Field(ge=30, le=720)
    preference_match: float = Field(ge=0,le=1)
    representativeness: float = Field(ge=0,le=1)
    tags: list[str]
    preferred_period: Literal['any','morning','afternoon','evening'] = 'any'
    draft_day: int = Field(ge=1)

class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requirements: Requirements
    candidates: list[Seed] = Field(min_length=1,max_length=24)
    day_rationale: list[str]
    uncertain_facts: list[str]


def variant():
    value=os.getenv('PLANNING_VARIANT','A').upper()
    if value not in {'A','B','C'}:raise ValueError('PLANNING_VARIANT must be A, B or C')
    return value


def identity(name, city=''):
    name=re.sub(r'[\s·()（）\-—]', '', name or '')
    if city and name.startswith(city):name=name[len(city):]
    return re.sub(r'(?:旅游景区|民俗风貌区|旅游度假区|旅游区|度假区|风景名胜区|风景区|景区|乐园)$','',name)


def same_entity(a,b,city=''):
    aa,bb=identity(a,city),identity(b,city)
    return bool(aa and bb and aa==bb)


def subpoint(name):
    return is_facility(name)


def choose_poi(name,aliases,rows,city,*,allow_model_alias=True):
    options={}
    for raw in rows:
        n=raw.get('name',''); spot=poi_to_spot(raw)
        if not spot or subpoint(n):continue
        provider_aliases=raw.get('alias') or []
        if isinstance(provider_aliases,str):provider_aliases=provider_aliases.split('|')
        provider_aliases=[a.strip() for a in provider_aliases if isinstance(a,str) and a.strip()] if isinstance(provider_aliases,list) else []
        # A model-suggested alias is a search hint, not equivalent evidence to
        # the user's actual name. It must not create a tie with an exact match.
        if same_entity(n,name,city):
            tier,score,basis=3,1.0,'primary_name_exact'
        elif any(same_entity(a,name,city) for a in provider_aliases):
            tier,score,basis=2,1.0,'provider_alias_exact'
        elif allow_model_alias and any(same_entity(n,a,city) for a in aliases):
            tier,score,basis=1,1.0,'model_alias_unverified'
        else:
            tier,score,basis=0,SequenceMatcher(None,identity(n,city),identity(name,city)).ratio(),'primary_name_similarity'
        if raw.get('parent'):score-=.08
        if score>=.80:
            key=spot.get('id') or (n,raw.get('location'),str(raw.get('address','')))
            option=(tier,score,spot,raw,basis,provider_aliases)
            if key not in options or option[:2]>options[key][:2]:options[key]=option
    options=sorted(options.values(),key=lambda x:(-x[0],-x[1],str(x[2].get('id'))))
    if not options:return None,{'query':name,'status':'unresolved','returned_names':[r.get('name') for r in rows]}
    if len(options)>1 and options[0][:2]==options[1][:2]:
        return None,{'query':name,'status':'ambiguous','returned_names':[x[2]['name'] for x in options[:4]],
                    'returned_ids':[x[2].get('id') for x in options[:4]]}
    _,score,spot,raw,basis,provider_aliases=options[0]
    spot.update(entity_id=entity_id(spot), parent_id=str(raw.get('parent') or '') or None)
    return spot,{'query':name,'aliases':aliases,'alias_status':'model_suggestions_not_verified',
                'provider_aliases':provider_aliases,'provider_alias_source':'amap:poi:'+str(spot.get('id') or ''),
                'match_basis':basis,'status':'resolved','poi_id':spot.get('id'),'entity_id':entity_id(spot),
                'parent_id':spot.get('parent_id'),'name':spot['name'],'match_score':score,
                'type':raw.get('type'),'address':raw.get('address'),
                'basis':'ranked primary name, city-limited provider query, independent destination; model aliases are unverified hints'}


def minutes(t):
    m=re.fullmatch(r'(\d{1,2}):(\d{2})',str(t or ''))
    return int(m[1])*60+int(m[2]) if m and int(m[1])<24 and int(m[2])<60 else None


def deterministic_limits(req,query):
    # The latest explicit numeric bounds remain hard even if a dialogue tool labels them prefer.
    times=re.findall(r'(\d{1,2}:\d{2})\s*(?:以后|之后|后).*?(?:开始|出门)?',query)
    valid=[minutes(t) for t in times if minutes(t) is not None]
    if valid:req.earliest_start=f'{max(valid)//60:02}:{max(valid)%60:02}'
    nums=re.findall(r'(?:每天|每日)[^。；;]*?(?:最多|不超过)\s*(\d+)\s*个',query)
    if nums:req.max_per_day=min(map(int,nums))
    if re.search(r'(?:不去|不要|不安排)主题乐园',query) and 'themepark' not in req.forbidden_tags:req.forbidden_tags.append('themepark')
    if re.search(r'(?:不安排|不要|不去)爬山',query) and 'climbing' not in req.forbidden_tags:req.forbidden_tags.append('climbing')
    if any(t in query for t in ['慢节奏','少走路','不赶','节奏放缓','不要紧凑']):req.slow=True
    return req


async def structured(schema,system,payload):
    from app.planning import nodes
    from app.planning.helpers import ainvoke_structured
    llm=nodes.build_structured_llm(schema,provider='deepseek',model=os.getenv('PLANNING_CANDIDATE_MODEL') or os.getenv('PLANNING_AGENT_MODEL'),temperature=0)
    messages=[('system',system),('human',json.dumps(payload,ensure_ascii=False,default=str))]
    for attempt in range(2):
        try:
            return await asyncio.wait_for(ainvoke_structured(llm,messages),timeout=90)
        except (ValidationError, OutputParserException) as exc:
            if attempt:raise
            messages += [('human', '上次结构化输出校验失败，请按原始要求重新生成完整结果；不得放松需求或静默截断时长。校验信息：'+str(exc))]


async def prepare(state):
    mode=variant()
    payload={'request':state.planning_instruction or state.query,'confirmed_constraints':state.effective_constraints,'destination':state.destination,'days':state.days,'dates':[str(state.travel_start_date),str(state.travel_end_date)]}
    instruction='从本次请求与确认需求提取完整硬约束。require/avoid不可降为prefer；本次要求优先于过去喜好。只把明确要求写成硬条件。已去过且不再安排的实体列入avoid_names。不要把兴趣变成必去。不早起有具体时间则提取。名称使用用户原词。保留需求证据。'
    proposal=None
    if mode=='C':
        instruction+=' 为此用户提出城市经典/主题候选与分日区域草案，不生成分钟表。优先适合用户的代表性热门，允许主题取舍。每个目的地给理由、准确全称与常用别名；不列入口、内部展厅、餐厅为景点。大型乐园/大型博物馆留足时间，不为凑数量压缩。候选覆盖各天并有少量备选，最多24项。避免为经典覆盖牺牲禁止项、体力和晚起。draft_day仅区域草案；representativeness是模型判断，不是已查证热度。不得捏造已核实开放/交通/预约信息；未知项列uncertain_facts。适用tags含history,museum,architecture,photo,child,themepark,climbing,high_fatigue,relaxed,indoor,outdoor。'
        proposal=await structured(Proposal,instruction,payload);req=proposal.requirements
    else:req=await structured(Requirements,instruction,payload)
    req=deterministic_limits(req,state.query or '')
    return {'planning_variant':mode,'hard_requirements':req.model_dump(),'planning_draft':proposal.model_dump() if proposal else {},'max_candidate_repair_rounds':0 if mode=='C' else 2,'habit_preference':('慢节奏' if req.slow else re.sub(r'不要紧凑|不紧凑','',state.habit_preference or '')),'history':state.history+[f'{mode}: requirements compiled; source=current confirmed brief']}


async def lookup(city,name,aliases=(),must=False):
    from app.planning.nodes import amap_key
    rows=await helpers.search_attraction_pois_async(city,amap_key(),keywords=name,types='',offset=12)
    spot,ev=choose_poi(name,list(aliases),rows,city,allow_model_alias=not must)
    if not spot and (aliases or must):
        extra=await helpers.search_attraction_pois_async(city,amap_key(),keywords=aliases[0] if aliases and not must else city+' '+name,types='',offset=12)
        spot,ev=choose_poi(name,list(aliases),rows+extra,city,allow_model_alias=not must)
    return spot,ev


async def search(state):
    from app.planning.nodes import attraction_search_node
    req=Requirements.model_validate(state.hard_requirements);mode=state.planning_variant
    pois=[];evidence=[];warnings=list(state.candidate_builder_warnings);resolved={}
    if mode=='B':
        base=await attraction_search_node(state);pois=base['pois']
    seeds=[Seed.model_validate(x) for x in state.planning_draft.get('candidates',[])] if mode=='C' else []
    queries=[(n,[]) for n in req.must_names]+[(s.name,s.aliases) for s in seeds]
    unique={}
    for n,a in queries:
        # Requirements are inserted first; a later model seed cannot overwrite
        # their primary-name lookup with its own unverified alias list.
        unique.setdefault(n,a)
    # Bound parallelism to the provider quota; benchmark trials themselves remain serial.
    for name,aliases in unique.items():
        try:spot,ev=await lookup(state.destination,name,aliases,must=name in req.must_names)
        except Exception as exc:spot=None;ev={'query':name,'status':'service_error','error':type(exc).__name__};warnings.append('POI_LOOKUP_FAILED:'+name)
        evidence.append(ev)
        if spot:pois.append(spot);resolved[name]=spot['name']
    if mode=='C' and len(pois)<max(state.days*2,4):
        # Query relevant topic rather than giving food/night markets an unconditional quota.
        from app.planning.nodes import amap_key
        topic='博物馆' if any(t in (state.attraction_preference or '') for t in ['历史','博物馆']) else '景点'
        raw=await helpers.search_attraction_pois_async(state.destination,amap_key(),keywords=f'{state.destination}{topic}',types='',offset=20)
        for r in raw:
            p=poi_to_spot(r)
            if p and not subpoint(p['name']):pois.append(p)
        warnings.append('RELEVANT_SEARCH_SUPPLEMENT')
    pois=list({p.get('id') or p['name']:p for p in pois}.values())
    constraints=list(state.effective_constraints)
    for n in req.must_names:
        matched=resolved.get(n) or next((p['name'] for p in pois if same_entity(n,p['name'],state.destination)),None)
        if matched:constraints.append({'id':'resolved-must:'+n,'polarity':'require','value_text':'必须去'+matched})
    for n in req.avoid_names:
        for p in pois:
            if same_entity(n,p['name'],state.destination):constraints.append({'id':'resolved-avoid:'+n,'polarity':'avoid','value_text':'不去'+p['name']})
    return {'pois':pois,'entity_evidence':evidence,'effective_constraints':constraints,'candidate_builder_warnings':warnings,'history':state.history+[f'{mode}: grounded {len(pois)} POIs; unresolved named requirements remain in ledger']}


def forbidden(candidate,req,city):
    n=candidate['poi_name'];tags=set(candidate.get('semantic_tags',[]));text=n+' '+candidate.get('poi_type','')
    if any(same_entity(n,a,city) for a in req.avoid_names):return True
    if 'themepark' in req.forbidden_tags and ('themepark' in tags or any(t in text for t in ['主题乐园','游乐园','主题公园','游乐场'])):return True
    if 'climbing' in req.forbidden_tags and ('climbing' in tags or '登山' in text):return True
    return bool(tags&set(req.forbidden_tags))


async def candidates(state):
    from app.planning.nodes import make_candidate_builder_node
    req=Requirements.model_validate(state.hard_requirements)
    warnings=list(state.candidate_builder_warnings)
    if state.planning_variant=='B':
        result=await make_candidate_builder_node(state.model_name)(state);pool=result['candidate_pool'];warnings=result.get('candidate_builder_warnings',warnings)
    else:
        pool=[];by_name={p['name']:p for p in state.pois}
        links={e['query']:e.get('name') for e in state.entity_evidence if e['status']=='resolved'}
        for raw in state.planning_draft.get('candidates',[]):
            s=Seed.model_validate(raw);p=by_name.get(links.get(s.name))
            if not p:continue
            proposal=CandidateAttraction(poi_name=p['name'],duration_min=s.duration_min,preference_match=s.preference_match,representativeness=s.representativeness,preferred_period=s.preferred_period,semantic_tags=s.tags)
            c=link_candidate_to_poi(proposal,p,cluster_id=s.draft_day-1).model_dump()
            c['semantic_evidence']+=['representativeness:model_judgment',s.reason,'opening/booking:not_independently_verified']
            pool.append(c)
        # A resolved must is never silently dropped just because the seed model omitted it.
        for n in req.must_names:
            p=by_name.get(links.get(n))
            if p and not any(c['poi_name']==p['name'] for c in pool):
                pool.append(link_candidate_to_poi(CandidateAttraction(poi_name=p['name'],duration_min=120,preference_match=1,representativeness=.5),p).model_dump())
                warnings.append('MUST_DURATION_UNVERIFIED:'+p['name'])
        if len(pool)<state.days:
            fallback=await make_candidate_builder_node(state.model_name)(state);pool=fallback['candidate_pool'];warnings+=fallback.get('candidate_builder_warnings',[])+['GROUNDED_SEMANTICS_FALLBACK']
    if state.planning_variant=='C':
        by_name={p['name']:p for p in state.pois}
        for c in pool:
            p=by_name.get(c['poi_name'],{})
            c.update(entity_id=entity_id(c),parent_id=p.get('parent_id'),
                entity_aliases=[e['query'] for e in state.entity_evidence if e.get('name')==c['poi_name']],
                opening_calendar=opening_calendar(p,state.travel_start_date,state.days))
        pool=merge_candidates(pool)
    bounded=[]
    for c in pool:
        if forbidden(c,req,state.destination):continue
        if state.planning_variant=='C' and (subpoint(c['poi_name']) or str(c.get('typecode','')).startswith('05')) and not any(same_entity(c['poi_name'],n,state.destination) for n in req.must_names):continue
        c['earliest_start_min']=minutes(req.earliest_start)
        c['must_visit']=c.get('must_visit',False) or any(same_entity(c['poi_name'],n,state.destination) or any(e.get('query')==n and e.get('name')==c['poi_name'] for e in state.entity_evidence) for n in req.must_names)
        bounded.append(c)
    return {'candidate_pool':bounded,'candidate_pool_fingerprint':candidate_pool_fingerprint(bounded),'candidate_builder_warnings':warnings,'candidate_pool_frozen':True}


async def transport_estimate(city,left,right,mode):
    from app.planning.nodes import amap_key
    def loc(p):return f"{p['lng']},{p['lat']}"
    params={'key':amap_key(),'origin':loc(left['location']),'destination':loc(right['location']),'output':'JSON'}
    if mode=='transit':endpoint='transit/integrated';params.update(city=city,cityd=city,strategy='0')
    else:endpoint='walking' if mode=='walking' else 'driving'
    await asyncio.sleep(.35)
    for attempt in range(3):
        payload=await http_get_json_async('https://restapi.amap.com/v3/direction/'+endpoint+'?'+urlencode(params))
        if payload.get('info')!='CUQPS_HAS_EXCEEDED_THE_LIMIT':break
        await asyncio.sleep(.8*(attempt+1))
    route=payload.get('route') or {};options=route.get('transits' if mode=='transit' else 'paths') or []
    durations=[int(x['duration']) for x in options if str(x.get('duration','')).isdigit()]
    if not durations and mode=='transit' and payload.get('info')=='OK':
        return await transport_estimate(city,left,right,'walking')
    return {'from':left['name'],'to':right['name'],'mode':mode,'status':'estimated' if durations else 'unverified','duration_minutes':math.ceil(min(durations)/60) if durations else None,'buffer_minutes':15,'provider_status':payload.get('info'),'queried_for':'current service estimate, not a future timetable guarantee'}


def edge_evidence(evidence, left, right, mode):
    for e in reversed(evidence):
        if e.get('from_id')==entity_id(left) and e.get('to_id')==entity_id(right) and e.get('requested_mode',e.get('mode'))==mode:
            if e.get('expires_at') and datetime.fromisoformat(e['expires_at'])<=datetime.now(timezone.utc):continue
            return e
    return None


def hydrate_transport(pool, evidence, mode):
    pool=deepcopy(pool)
    for left in pool:
        left['transfer_minutes_to']={}
        left['allowed_transfer_names']=None
        for right in pool:
            e=edge_evidence(evidence,left,right,mode)
            if e and e.get('duration_minutes') is not None:
                left['transfer_minutes_to'][right['poi_name']]=max(20,e['duration_minutes']+e.get('buffer_minutes',15))
    return pool


def restrict_transport(pool, evidence, mode, *, verified_only):
    """Final repair uses only observed edges; never promotes optional POIs to musts."""
    pool = deepcopy(pool)
    for left in pool:
        left['allowed_transfer_names'] = []
        for right in pool:
            edge = edge_evidence(evidence, left, right, mode)
            if edge and (not verified_only or (edge.get('status') == 'estimated' and edge.get('duration_minutes') is not None)):
                left['allowed_transfer_names'].append(right['poi_name'])
    return pool


def evidence_aware_periods(pool, state):
    """Prefer an early visit for unverified indoor venues, never invent opening hours.

    This replaces only a model-generated preference. User time instructions and
    fixed starts take precedence; all missing evidence remains explicitly pending.
    """
    pool = deepcopy(pool)
    req = Requirements.model_validate(state.hard_requirements)
    text = ' '.join([state.query or '', state.planning_instruction or '',
                     *[str(c.get('value_text') or '') for c in state.effective_constraints]])
    if re.search(r'下午|傍晚|晚上|夜间|夜游|午后|中午', text) or (minutes(req.earliest_start) or 0) >= 720:
        return pool
    for c in pool:
        rules = (c.get('opening_calendar') or {}).get('days', {})
        venue = c.get('category') == 'museum' or 'indoor' in c.get('semantic_tags', []) or bool(
            re.search(r'博物馆|纪念馆|展览馆|寺庙|道观|宗教', c.get('poi_type', '')))
        if venue and rules and all(r.get('status') == 'unknown' for r in rules.values()) and c.get('fixed_start_min') is None:
            c['preferred_period'] = 'morning'
            c['semantic_evidence'] = list(dict.fromkeys(c.get('semantic_evidence', []) +
                ['planning_heuristic:early_visit_when_venue_hours_unknown;not_verified_opening']))
    return pool


async def optimize(state):
    from app.planning.nodes import optimize_attractions_node
    req=Requirements.model_validate(state.hard_requirements)
    constraints=list(state.effective_constraints)
    if req.max_per_day:constraints.append({'polarity':'require','value_text':f'每天最多{req.max_per_day}个景点'})
    evidence=deepcopy(state.transport_evidence)
    pool=hydrate_transport(evidence_aware_periods(state.candidate_pool,state),evidence,req.transport) if state.planning_variant=='C' else state.candidate_pool
    working=state.model_copy(update={'effective_constraints':constraints,'candidate_pool':pool})
    result=await optimize_attractions_node(working)
    rounds=state.transport_repair_round
    trace=list(state.transport_repair_trace)
    if state.planning_variant=='C':
        while result.get('route') and rounds<2:
            rounds+=1
            by_name={c['poi_name']:c for c in pool}
            for day in result['route']:
                for left,right in zip(day['spots'],day['spots'][1:]):
                    a,b=by_name[left['name']],by_name[right['name']]
                    if edge_evidence(evidence,a,b,req.transport):continue
                    try:
                        e=await transport_estimate(state.destination,{**left,'location':a['location']},{**right,'location':b['location']},req.transport)
                    except Exception as exc:
                        e={'from':left['name'],'to':right['name'],'status':'unverified','error':type(exc).__name__}
                    queried=datetime.now(timezone.utc)
                    e.update(from_id=entity_id(a),to_id=entity_id(b),requested_mode=req.transport,
                        queried_at=queried.isoformat(),expires_at=(queried+timedelta(seconds=86400 if e.get('mode')=='walking' else 900)).isoformat())
                    evidence.append(e)
            pool=hydrate_transport(pool,evidence,req.transport)
            if rounds == 2:
                pool = restrict_transport(pool, evidence, req.transport, verified_only=True)
            working=working.model_copy(update={'candidate_pool':pool,'transport_evidence':evidence})
            # Re-solve the whole candidate set. Only the user's musts remain musts.
            result=await optimize_attractions_node(working)
            if rounds == 2 and not (result.get('route') and (result.get('quality_report') or {}).get('passed')):
                trace.append({'round': rounds, 'action': 'estimated_edges_only_infeasible_or_rejected',
                              'diagnostics': result.get('quality_report')})
                # Service failures remain explicitly unknown. Permit those
                # attempted edges, but never introduce a new unqueried edge.
                pool = restrict_transport(pool, evidence, req.transport, verified_only=False)
                working = working.model_copy(update={'candidate_pool': pool})
                result = await optimize_attractions_node(working)
            trace.append({'round': rounds, 'action': 'reoptimized',
                          'edge_policy': 'observed_only' if rounds == 2 else 'allow_discovery',
                          'route_present': bool(result.get('route'))})
            combined=working.model_copy(update={**result,'candidate_pool':pool})
            if result.get('route') and (result.get('quality_report') or {}).get('passed'):
                all_edges=all((edge_evidence(evidence,
                    next(c for c in pool if c['poi_name']==a['name']),
                    next(c for c in pool if c['poi_name']==b['name']),req.transport) or {}).get('duration_minutes') is not None
                    for d in result['route'] for a,b in zip(d['spots'],d['spots'][1:]))
                if all_edges and not hard_violations(combined):break
    result.update(candidate_pool=pool,candidate_pool_fingerprint=candidate_pool_fingerprint(pool),
        transport_evidence=evidence,effective_constraints=constraints,transport_repair_round=rounds,
        transport_repair_trace=trace)
    return result


def validation(state, final_plan=None):
    from app.planning.optimizer import _start_intervals
    req=Requirements.model_validate(state.hard_requirements)
    route=state.route if final_plan is None else [
        {'day':d['day'],'spots':[s for s in d.get('timeline',[]) if s.get('type')=='attraction']}
        for d in final_plan.get('days',[])]
    selected=[s for d in route for s in d['spots']]
    pool={c['poi_name']:c for c in state.candidate_pool}
    resolved={e['query']:e.get('name') for e in state.entity_evidence if e['status']=='resolved'}
    hard=[];pending=[];seen=set()
    for n in req.must_names:
        if not any(same_entity(s['name'],n,state.destination) or s['name']==resolved.get(n) or
            n in pool.get(s['name'],{}).get('entity_aliases',[]) for s in selected):
            hard.append('MISSING_MUST:'+n)
    if route and ([d['day'] for d in route]!=list(range(1,state.days+1))):hard.append('INVALID_DAY_SEQUENCE')
    for day in route:
        if not day['spots']:hard.append('EMPTY_DAY:'+str(day['day']))
        if req.max_per_day and len(day['spots'])>req.max_per_day:hard.append('DAILY_MAX:'+str(day['day']))
        visit=state.travel_start_date+timedelta(days=day['day']-1) if state.travel_start_date else None
        previous_end=None
        for spot in day['spots']:
            name=spot['name'];c=pool.get(name,{'poi_name':name})
            key=entity_id(c)
            if key in seen:hard.append('DUPLICATE_ENTITY:'+name)
            seen.add(key)
            if name not in pool:hard.append('UNKNOWN_CANDIDATE:'+name)
            if forbidden(c,req,state.destination) or any(resolved.get(n)==name for n in req.avoid_names):hard.append('AVOID:'+name)
            start,end=minutes(spot.get('start_time')),minutes(spot.get('end_time'))
            if start is None or end is None or end<=start:
                hard.append('INVALID_TIME:'+name);continue
            if previous_end is not None and start<previous_end:hard.append('TIME_OVERLAP:'+name)
            previous_end=end
            if minutes(req.earliest_start) is not None and start<minutes(req.earliest_start):hard.append('EARLY_START:'+name)
            if c.get('duration_min') and end-start!=c['duration_min']:hard.append('DURATION_MISMATCH:'+name)
            calendar=c.get('opening_calendar')
            if calendar is not None:
                rule=calendar.get('days',{}).get(visit.isoformat() if visit else '',{})
                if rule.get('status') not in ('known','closed'):
                    pending.append({'kind':'opening','name':name,'day':day['day'],'reason':rule.get('reason','missing_date')})
                if not any(lo<=start<=hi for lo,hi in _start_intervals(c,visit)):
                    hard.append('OPENING_CONFLICT:'+name)
            else:pending.append({'kind':'opening','name':name,'day':day['day'],'reason':'not_structured'})
        for left,right in zip(day['spots'],day['spots'][1:]):
            a,b=pool.get(left['name'],{'poi_name':left['name']}),pool.get(right['name'],{'poi_name':right['name']})
            if a.get('allowed_transfer_names') is not None and right['name'] not in a['allowed_transfer_names']:
                hard.append('UNQUERIED_TRANSPORT:'+left['name']+'→'+right['name'])
            e=edge_evidence(state.transport_evidence,a,b,req.transport)
            if e and e.get('duration_minutes') is not None:
                start,end=minutes(right.get('start_time')),minutes(left.get('end_time'))
                if start is not None and end is not None and start-end<e['duration_minutes']+e.get('buffer_minutes',15):
                    hard.append('TRANSPORT_TIME:'+left['name']+'→'+right['name'])
            else:pending.append({'kind':'transport','from':left['name'],'to':right['name'],'reason':'no_verified_estimate'})
    if final_plan is not None and route!=state.route:
        expected=[(d['day'],[(s['name'],s.get('start_time'),s.get('end_time')) for s in d['spots']]) for d in state.route]
        actual=[(d['day'],[(s['name'],s.get('start_time'),s.get('end_time')) for s in d['spots']]) for d in route]
        if actual!=expected:hard.append('FINAL_ROUTE_MISMATCH')
    return {'status':'failed' if hard or not route else 'needs_verification' if pending else 'supported_checks_passed',
        'hard_violations':sorted(set(hard)),'pending_checks':pending,
        'scope':'Confirmed requirements, entity uniqueness, supplied opening rules and available transport estimates; no guarantee of future traffic, booking availability or accessibility.'}


def hard_violations(state):
    return validation(state)['hard_violations']


def quality(state):
    from app.planning.nodes import quality_gate_node, PlanningQualityError
    if not state.route:
        raise PlanningQualityError('NO_FEASIBLE_PLAN: 未找到满足本次要求的可行方案；不能据此断言需求无解。')
    summary=validation(state)
    if summary['hard_violations']:
        raise PlanningQualityError('Confirmed requirements not satisfied: '+','.join(summary['hard_violations']))
    result=quality_gate_node(state)
    result['validation_summary']=summary
    return result
