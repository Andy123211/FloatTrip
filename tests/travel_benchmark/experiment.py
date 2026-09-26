"""Frozen three-arm execution and blind, paired comparison. Scores are never generated here."""
from __future__ import annotations
import argparse
from collections import defaultdict
from itertools import combinations
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
from .common import ROOT, DATA, read, write, digest, now
from .evidence import Catalog, evaluate, quality_verdict, norm

SEED=20260919

def initialize_cache_policy(out):
    """New experiments default to shared exact-request reuse, not cold per trial."""
    import hashlib
    from app.core.amap_cache import TTLS, POLICY
    directory=out/'cache_policy_v1';directory.mkdir(parents=True,exist_ok=True)
    for source in [ROOT/'app/core/amap_cache.py',Path(__file__).with_name('cache_worker.py')]:
        shutil.copy2(source,directory/source.name)
    write(directory/'policy.json',{'id':POLICY,'created_at':now(),'first_schedule_index':1,'completed_before_change':0,
        'ttls_seconds':TTLS,'namespace':'shared exact-request cache inside this experiment only',
        'code_hashes':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in directory.glob('*.py')},
        'reason':'Default quota-conserving policy requested by user. All arms share TTL and exact-request keys.',
        'cache_inputs':'Successful Amap HTTP responses only; no benchmark answers.'})

def initialize(out):
    if (out/'experiment.json').exists():raise SystemExit('Experiment is already frozen; use run to resume')
    baseline=out/'baseline'
    if not (baseline/'app').exists():raise SystemExit('Baseline code snapshot is required before implementation')
    data=DATA/'four_city_v2'
    frozen=out/'frozen';shutil.copytree(data,frozen)
    cases=read(frozen/'cases.json')['cases'];schedule=[];rng=random.Random(SEED)
    for repeat in range(1,4):
        shuffled=list(cases);rng.shuffle(shuffled)
        for index,c in enumerate(shuffled):
            arms=['A','B','C'];offset=(index+repeat)%3;arms=arms[offset:]+arms[:offset]
            for arm in arms:schedule.append({'case_id':c['id'],'city':c['city'],'mode':'planning','trial':repeat,'variant':arm})
    for c in cases:
        if c['dialogue']:
            arms=['A','B','C'];rng.shuffle(arms)
            for arm in arms:schedule.append({'case_id':c['id'],'city':c['city'],'mode':'dialogue','trial':1,'variant':arm})
    assert len(schedule)==456
    write(out/'schedule.json',schedule)
    from app.core.env import load_local_env
    load_local_env()
    for arm in ['A','B','C']:
        runtime=out/'runtimes'/arm;runtime.mkdir(parents=True)
        source=baseline if arm=='A' else ROOT
        shutil.copytree(source/'app',runtime/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (runtime/'tests').mkdir();(runtime/'tests/__init__.py').write_text('')
        shutil.copytree(ROOT/'tests/travel_benchmark',runtime/'tests/travel_benchmark',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        run=out/'runs'/arm;run.mkdir(parents=True)
        shutil.copytree(frozen,run/'frozen')
        env=dict(os.environ,PLANNING_VARIANT=arm)
        manifest=__import__('json').loads(subprocess.check_output([sys.executable,'-c','import json;from tests.travel_benchmark.common import provenance;print(json.dumps(provenance()))'],cwd=runtime,env=env,text=True))
        manifest.update(planning_variant=arm,benchmark_hash=digest({p.name:read(p) for p in frozen.glob('*.json')}),cache_policy='shared local HTTP cache with TTL; isolated from production; legacy Redis remains trial-isolated')
        write(run/'manifest.json',manifest)
        write(run/'schedule.json',[{k:v for k,v in r.items() if k in ['case_id','mode','trial']} for r in schedule if r['variant']==arm])
    write(out/'experiment.json',{'created_at':now(),'seed':SEED,'count':456,'development_cities':['上海','南京'],'holdout_cities':['北京','重庆'],'benchmark_hash':digest(read(frozen/'freeze.json')),'default_before':'A','gate':read(frozen/'freeze.json')['gate'],'note':'All production runtimes are frozen copies. A app is the pre-implementation dirty-worktree snapshot; common evidence instrumentation is shared.'})
    initialize_cache_policy(out)
    print('Frozen A/B/C runtimes and 456 trial schedule',flush=True)


def run(out):
    from app.core.env import load_local_env
    load_local_env();schedule=read(out/'schedule.json');cases={c['id']:c for c in read(out/'frozen/cases.json')['cases']};cat=Catalog(read(out/'frozen/catalog.json'))
    for index,s in enumerate(schedule):
        arm=s['variant'];base=out/'runs'/arm;key=f'{s["case_id"]}.{s["mode"]}.{s["trial"]}';path=base/'trials'/f'{key}.json'
        if path.exists() and read(path).get('status') in ['succeeded','failed','timeout','interrupted']:
            if not (base/'evidence'/f'{key}.json').exists():
                write(base/'evidence'/f'{key}.json',evaluate(read(path),cases[s['case_id']],cat))
            continue
        if path.exists():
            r=read(path);r.update(status='interrupted',finished_at=now());write(path,r)
        else:
            env=dict(os.environ,PLANNING_VARIANT=arm,BENCHMARK_CACHE_NAMESPACE=f'benchmark:{out.name}:{arm}:{key}')
            cmd=[sys.executable,'-m','tests.travel_benchmark','worker','--out',str(base),'--case',s['case_id'],'--mode',s['mode'],'--trial',str(s['trial'])]
            policy_path=out/'cache_policy_v1/policy.json'
            if policy_path.exists():
                policy=read(policy_path)
                env['AMAP_CACHE_PATH']=str(out/'cache_policy_v1/responses.sqlite')
                env['AMAP_CACHE_ENABLED']='1'
                cmd=[sys.executable,str(out/'cache_policy_v1/cache_worker.py'),*cmd[3:]]
            print(f'{index+1}/456 RUN {arm} {key}',flush=True)
            try:
                proc=subprocess.run(cmd,cwd=out/'runtimes'/arm,env=env,capture_output=True,text=True,timeout=600)
                write(base/'logs'/f'{key}.json',{'returncode':proc.returncode,'stdout':proc.stdout[-12000:],'stderr':proc.stderr[-12000:]})
                r=read(path) if path.exists() else {**s,'state':{},'status':'failed','error':{'type':'WorkerFailure','message':f'exit {proc.returncode}'}}
                if r['status']=='running':r.update(status='failed',error={'type':'WorkerFailure','message':f'exit {proc.returncode}'})
            except subprocess.TimeoutExpired:
                r=read(path) if path.exists() else {**s,'state':{}}
                r.update(status='timeout',finished_at=now(),elapsed_seconds=600,error={'type':'TimeoutError','message':'600 second process limit'})
            write(path,r)
        ev=evaluate(r,cases[s['case_id']],cat);write(base/'evidence'/f'{key}.json',ev)
        print(f'{index+1}/456 {r["status"].upper()} {arm} {key} {r.get("elapsed_seconds")}s',flush=True)
    packets(out)


def packets(out):
    cases={c['id']:c for c in read(out/'frozen/cases.json')['cases']};schedule=list(read(out/'schedule.json'));rng=random.Random(SEED+1);rng.shuffle(schedule)
    mapping={}
    for i,s in enumerate(schedule,1):
        uid=f'review-{i:03}';key=f'{s["case_id"]}.{s["mode"]}.{s["trial"]}';base=out/'runs'/s['variant'];p=base/'trials'/f'{key}.json'
        if not p.exists() or not (base/'evidence'/f'{key}.json').exists():continue
        r=read(p);ev=read(base/'evidence'/f'{key}.json');c=cases[s['case_id']]
        mapping[uid]={**s,'key':key}
        # No stage names, arm, internal score, raw model reasoning or identifying source paths.
        packet={'id':uid,'city':c['city'],'request':c['input']['query'],'mode':s['mode'],'status':r['status'],'expectations':c['expectations'],'evidence':{k:v for k,v in ev.items() if k not in ['stage_names','missed_hot']},'planning_notes':(r.get('state',{}).get('final_plan') or {}).get('planning_notes',[]),'transport_evidence':r.get('state',{}).get('transport_evidence',[]),'failure_type':(r.get('error') or {}).get('type')}
        packet['output_details']=[{'day':day.get('day'),**{k:spot.get(k) for k in ['name','tip','open_time','address']}} for day in (r.get('state',{}).get('final_plan') or {}).get('days',[]) for spot in day.get('timeline',[]) if spot.get('type')=='attraction']
        if s['mode']=='dialogue':
            packet['dialogue']=[{'user':t.get('user'),'assistant':[m['content'] for m in t.get('messages',[]) if m.get('type')=='ai' and isinstance(m.get('content'),str) and m['content'] and not m.get('tool_calls')]} for t in r.get('dialogue_turns',[])]
            snapshot=r.get('submitted_snapshot') or {}
            packet['understood_requirements']={key:snapshot[key] for key in c['input'] if key in snapshot}
            if not packet['dialogue'] and p.with_suffix('.sqlite').exists():
                # On an exception before ainvoke returns, the frozen recorder has
                # no turn result. Recover only persisted public inputs, read-only.
                import sqlite3, json
                with sqlite3.connect(p.with_suffix('.sqlite').as_uri()+'?mode=ro',uri=True) as db:
                    messages=db.execute('SELECT role,content FROM messages ORDER BY sequence').fetchall()
                    briefs=db.execute('SELECT submission_snapshot_json FROM planning_briefs ORDER BY updated_at DESC LIMIT 1').fetchall()
                packet['dialogue']=[{'user':content,'assistant':[]} for role,content in messages if role=='user']
                if briefs and briefs[0][0]:
                    snapshot=json.loads(briefs[0][0]);packet['understood_requirements']={key:snapshot[key] for key in c['input'] if key in snapshot}
                packet['dialogue_capture_note']='Recovered persisted user messages and submission snapshot from isolated SQLite after graph failure. No final assistant response was delivered; no transcript invented.'
        write(out/'blind_packets'/f'{uid}.json',packet)
    write(out/'blind_mapping.json',mapping)
    print('Prepared',len(mapping),'anonymous review packets',flush=True)


def mean(xs):return statistics.mean(xs) if xs else None

def interval(values):
    if not values:return None
    rng=random.Random(SEED);boots=sorted(mean(rng.choices(values,k=len(values))) for _ in range(2000))
    return [round(boots[49],3),round(boots[1949],3)]


def service_sensitivity(rows, schedule, incident):
    """Secondary estimate excludes whole matched blocks, never changes primary results."""
    if not incident:return None
    first,last=incident['affected_schedule_range']
    affected=list(schedule[first-1:last])+[schedule[i-1] for i in incident.get('additional_schedule_indices',[])]
    blocks={(s['case_id'],s['mode'],s['trial']) for s in affected}
    retained=[r for r in rows if (r['case_id'],r['mode'],r['trial']) not in blocks]
    paired=[]
    for city in ['上海','南京','北京','重庆']:
        for newer,older in [('B','A'),('C','A'),('C','B')]:
            deltas=[];passes=[];pairs_count=0
            for cid in sorted({r['case_id'] for r in retained if r['city']==city and r['mode']=='planning'}):
                arms={arm:{r['trial']:r for r in retained if r['case_id']==cid and r['variant']==arm and r['mode']=='planning'} for arm in [newer,older]}
                trials=sorted(set(arms[newer])&set(arms[older]))
                if not trials:continue
                deltas.append(mean([arms[newer][t]['score']-arms[older][t]['score'] for t in trials]))
                passes.append(mean([int(arms[newer][t]['passed'])-int(arms[older][t]['passed']) for t in trials]));pairs_count+=len(trials)
            paired.append({'city':city,'comparison':f'{newer}-{older}','paired_cases':len(deltas),'paired_repeats':pairs_count,'score_delta':mean(deltas),'score_ci95_case_bootstrap':interval(deltas),'pass_rate_delta':mean(passes),'pass_ci95_case_bootstrap':interval(passes)})
    return {'excluded_matched_blocks':len(blocks),'excluded_rows':len(rows)-len(retained),'paired':paired,'note':'Secondary only. Excludes all arms in the balance-outage blocks, including successful fallbacks. Primary failures and rollout gates remain unchanged. Cases receive equal weight; remaining repeats are averaged within each case.'}


def freeze_reviews(out):
    """Seal subjective judgments before the comparison reveals treatment labels."""
    packet_paths=sorted((out/'blind_packets').glob('review-*.json'))
    if len(packet_paths)!=456:raise SystemExit('All 456 anonymous packets are required')
    hashes={}
    for packet_path in packet_paths:
        review_path=out/'blind_reviews'/packet_path.name
        if not review_path.exists():raise SystemExit(f'Missing review: {packet_path.stem}')
        review=read(review_path);packet=read(packet_path)
        if not review.get('blind_to_variant') or not review.get('blind_to_internal_objective'):
            raise SystemExit(f'Unblinded review: {packet_path.stem}')
        quality_verdict(review,packet['evidence'],packet['status'])
        if any(value*2!=int(value*2) for value in review['scores'].values()):
            raise SystemExit(f'Scores must use 0.5 increments: {packet_path.stem}')
        for path in [packet_path,review_path]:hashes[str(path.relative_to(out))]=digest(read(path))
    for name in ['supplemental_review_sources.json','service_incident.json','cache_policy_v1/policy.json','cache_policy_v1/seed_manifest.json','cache_policy_v1/network_incident.json','infrastructure_sensitivity_policy.json']:
        supplemental=out/name
        if supplemental.exists():hashes[name]=digest(read(supplemental))
    for path in (out/'review_revisions').glob('**/*.json'):hashes[str(path.relative_to(out))]=digest(read(path))
    target=out/'reviews_freeze.json'
    if target.exists():
        if read(target)['hashes']!=hashes:raise SystemExit('Frozen review evidence has changed')
    else:write(target,{'created_at':now(),'count':456,'hashes':hashes,'note':'Manual Codex judgments sealed before treatment labels are inspected.'})
    print('456 anonymous reviews sealed')


def compare(out):
    if not (out/'reviews_freeze.json').exists():raise SystemExit('Run freeze-reviews before revealing group comparisons')
    for name,expected in read(out/'reviews_freeze.json')['hashes'].items():
        if digest(read(out/name))!=expected:raise SystemExit(f'Frozen review drift: {name}')
    mapping=read(out/'blind_mapping.json');rows=[]
    for uid,s in mapping.items():
        base=out/'runs'/s['variant'];r=read(base/'trials'/f'{s["key"]}.json');ev=read(base/'evidence'/f'{s["key"]}.json');p=out/'blind_reviews'/f'{uid}.json';review=read(p) if p.exists() else None
        v=quality_verdict(review,ev,r['status'])
        token_counts=[]
        for usage in r.get('model_usage',[]):
            output=(usage.get('llm_output') or {}).get('token_usage') or {}
            total=output.get('total_tokens')
            if total is None:
                metas=[m for m in usage.get('usage_metadata',[]) if m];total=sum(m.get('total_tokens',0) for m in metas) if metas else None
            if total is not None:token_counts.append(total)
        rows.append({**s,'review_id':uid,**v,'status':r['status'],'scores':review.get('scores') if review else None,'hard_violations':ev['hard_violations']+(review.get('additional_hard_violations',[]) if review else []),'coverage':ev.get('coverage',{}),'must_satisfied':ev.get('must_visit_satisfied',[]),'selected_groups':ev.get('selected_groups',[]),'selected_ids':ev.get('selected_ids',[]),'elapsed_seconds':r.get('elapsed_seconds'),'api_calls':len(r.get('api_calls',[])),'tokens_observed':sum(token_counts) if token_counts else None,'model_calls_observed':len(r.get('model_usage',[])),'degradation':r.get('state',{}).get('candidate_builder_warnings',[]),'internal_quality_passed':(r.get('state',{}).get('quality_report') or {}).get('passed'), 'unknown_poi_count':len(ev.get('unknown_pois',[]))})
        rows[-1]['selected_entities']=[f['match']['id'] if f['match']['kind']=='exact' else norm(f['name']) for f in ev.get('facts',[])]
        rows[-1]['api_calls']=sum(not bool(call.get('cache_hit')) for call in r.get('api_calls',[]))
        rows[-1]['amap_cache_hits']=sum(bool(call.get('cache_hit')) for call in r.get('api_calls',[]))
        rows[-1]['cache_policy']=r.get('cache_policy','per-trial-cold-v2')
        rows[-1]['failure_type']=(r.get('error') or {}).get('type')
    stats=[]
    for city in ['上海','南京','北京','重庆']:
        for arm in ['A','B','C']:
            rr=[r for r in rows if r['city']==city and r['variant']==arm and r['mode']=='planning'];scored=[r for r in rr if r['score'] is not None];times=sorted(r['elapsed_seconds'] for r in rr if r['elapsed_seconds'] is not None)
            stats.append({'city':city,'variant':arm,'scheduled':36,'completed':len(rr),'generated':sum(r['status']=='succeeded' for r in rr),'reviewed':len(scored),'passed':sum(r['passed'] for r in rr),'pass_rate':sum(r['passed'] for r in rr)/36,'mean_score':round(mean([r['score'] for r in scored]),2) if scored else None,'hard_violation_trials':sum(bool(r['hard_violations']) for r in rr),'dimensions':{k:round(mean([r['scores'][k] for r in scored]),2) if scored else None for k in ['popularity','personalization','route','validity']},'latency_median':round(statistics.median(times),2) if times else None,'latency_p95':times[min(len(times)-1,int(len(times)*.95))] if times else None,'api_calls':sum(r['api_calls'] for r in rr),'model_calls_observed':sum(r['model_calls_observed'] for r in rr),'tokens_observed':sum(r['tokens_observed'] or 0 for r in rr)})
    cases={c['id']:c for c in read(out/'frozen/cases.json')['cases']}
    stability=[];scenarios=[]
    for arm in ['A','B','C']:
        for cid,c in cases.items():
            rr=[r for r in rows if r['variant']==arm and r['case_id']==cid and r['mode']=='planning']
            pairs=[];entity_pairs=[]
            for a,b in combinations([r for r in rr if r['status']=='succeeded'],2):
                aa,bb=set(a['selected_groups']),set(b['selected_groups']);pairs.append(len(aa&bb)/len(aa|bb) if aa|bb else 1)
                aa,bb=set(a['selected_entities']),set(b['selected_entities']);entity_pairs.append(len(aa&bb)/len(aa|bb) if aa|bb else 1)
            scores=[r['score'] for r in rr if r['score'] is not None]
            stability.append({'variant':arm,'case_id':cid,'all_three_pass':len(rr)==3 and all(r['passed'] for r in rr),'any_pass':any(r['passed'] for r in rr),'group_jaccard':mean(pairs),'entity_jaccard':mean(entity_pairs),'score_std_population':statistics.pstdev(scores) if scores else None,'note':'Overlap measures successful outputs only. Entity overlap includes normalized unknown names; group overlap only catalog groups. Neither measures timing stability; failed trials remain in all-three-pass and score dispersion.'})
            scenarios.append({'variant':arm,'case_id':cid,'city':c['city'],'scenario':c['scenario'],'completed':len(rr),'passed':sum(r['passed'] for r in rr),'mean_score':mean([r['score'] for r in rr if r['score'] is not None])})
    for stat in stats:
        rr=[r for r in rows if r['city']==stat['city'] and r['variant']==stat['variant'] and r['mode']=='planning']
        stat['successful_itinerary_mean_score']=mean([r['score'] for r in rr if r['status']=='succeeded' and r['score'] is not None])
        stat['latency_observed_trials']=sum(r['elapsed_seconds'] is not None for r in rr)
        stat['latency_missing_trials']=sum(r['elapsed_seconds'] is None for r in rr)
        stat['token_usage_observed_trials']=sum(r['tokens_observed'] is not None for r in rr)
        stat['coverage_mean']={stage:mean([r['coverage'][stage]['fraction_of_eligible'] for r in rr if stage in r['coverage'] and r['coverage'][stage]['fraction_of_eligible'] is not None]) for stage in ['raw_search','merged_search','filtered','candidate','selected']}
        stat['must_requirements_total']=sum(len(cases[r['case_id']]['expectations']['must_visit']) for r in rr)
        stat['must_requirements_satisfied']=sum(len(r['must_satisfied']) for r in rr)
        named_avoid=[r for r in rr if cases[r['case_id']]['expectations']['avoid']]
        stat['named_avoid_trials']=len(named_avoid)
        stat['named_avoid_violating_trials']=sum(any(v.startswith(('AVOID:','AVOID_SUBPOINT:')) for v in r['hard_violations']) for r in named_avoid)
        stat['internal_quality_passed']=sum(bool(r['internal_quality_passed']) for r in rr)
        stat['fallback_trials']=sum(any('FALLBACK' in w or 'FAILED' in w for w in r['degradation']) for r in rr)
    paired=[]
    for city in ['上海','南京','北京','重庆']:
        for newer,older in [('B','A'),('C','A'),('C','B')]:
            case_deltas=[];pass_deltas=[]
            ids={r['case_id'] for r in rows if r['city']==city and r['mode']=='planning'}
            for cid in sorted(ids):
                x=[r for r in rows if r['case_id']==cid and r['variant']==newer and r['mode']=='planning'];y=[r for r in rows if r['case_id']==cid and r['variant']==older and r['mode']=='planning']
                if len(x)==len(y)==3 and all(r['score'] is not None for r in x+y):
                    case_deltas.append(mean([r['score'] for r in x])-mean([r['score'] for r in y]));pass_deltas.append(mean([int(r['passed']) for r in x])-mean([int(r['passed']) for r in y]))
            paired.append({'city':city,'comparison':f'{newer}-{older}','paired_cases':len(case_deltas),'score_delta':mean(case_deltas),'score_ci95_case_bootstrap':interval(case_deltas),'pass_rate_delta':mean(pass_deltas),'pass_ci95_case_bootstrap':interval(pass_deltas)})
    complete=len(rows)==456 and all(r['review_status']=='reviewed' for r in rows)
    checks={'all_456_reviewed':complete,'C_each_city_29_passes':all(x['passed']>=29 for x in stats if x['variant']=='C'),'C_no_confirmed_hard_violations':not any(r['hard_violations'] for r in rows if r['variant']=='C')}
    for city in ['上海','南京','北京','重庆']:
        a=next(x for x in stats if x['city']==city and x['variant']=='A');c=next(x for x in stats if x['city']==city and x['variant']=='C');strict=city in ['北京','重庆']
        checks[city+'_improvement']=bool(a['mean_score'] is not None and c['mean_score'] is not None and (c['mean_score']>a['mean_score'] and c['passed']>a['passed'] if strict else c['mean_score']>=a['mean_score'] and c['passed']>=a['passed']))
    cohorts=[]
    for label,cities in [('development',['上海','南京']),('holdout',['北京','重庆'])]:
        for arm in ['A','B','C']:
            rr=[r for r in rows if r['city'] in cities and r['variant']==arm and r['mode']=='planning']
            cohorts.append({'cohort':label,'variant':arm,'scheduled':72,'mean_score':mean([r['score'] for r in rr]),'pass_rate':sum(r['passed'] for r in rr)/72,'hard_violation_rate':sum(bool(r['hard_violations']) for r in rr)/72})
    dialogue_stats=[]
    for city in ['上海','南京','北京','重庆']:
        for arm in ['A','B','C']:
            rr=[r for r in rows if r['city']==city and r['variant']==arm and r['mode']=='dialogue']
            dialogue_stats.append({'city':city,'variant':arm,'scheduled':2,'generated':sum(r['status']=='succeeded' for r in rr),'mean_score':mean([r['score'] for r in rr]),'passed':sum(r['passed'] for r in rr),'hard_violation_trials':sum(bool(r['hard_violations']) for r in rr)})
    incident=read(out/'service_incident.json') if (out/'service_incident.json').exists() else None
    sensitivity=service_sensitivity(rows,read(out/'schedule.json'),incident)
    infrastructure=read(out/'infrastructure_sensitivity_policy.json') if (out/'infrastructure_sensitivity_policy.json').exists() else None
    combined_sensitivity=service_sensitivity(rows,read(out/'schedule.json'),infrastructure)
    if combined_sensitivity:
        combined_sensitivity['note']='Secondary only: excludes complete matched case/repeat blocks affected by balance outage or restricted-network operator error; primary results and rollout gates retain every failure.'
    cache_segments=[]
    for policy in sorted({r['cache_policy'] for r in rows}):
        for arm in ['A','B','C']:
            rr=[r for r in rows if r['cache_policy']==policy and r['variant']==arm]
            cache_segments.append({'cache_policy':policy,'variant':arm,'trials':len(rr),'network_responses':sum(r['api_calls'] for r in rr),'local_hits':sum(r['amap_cache_hits'] for r in rr),'latency_mean':mean([r['elapsed_seconds'] for r in rr if r['elapsed_seconds'] is not None]),'latency_note':'Mean, descriptive only; scenario/mode composition differs across segments, not an algorithm speedup estimate.'})
    gate=all(checks.values());write(out/'comparison.json',{'created_at':now(),'stats':stats,'paired':paired,'cohorts':cohorts,'dialogue_stats':dialogue_stats,'stability':stability,'scenarios':scenarios,'trials':rows,'gate_checks':checks,'eligible_to_switch_to_C':gate,'service_incident':incident,'service_available_sensitivity':sensitivity,'cost_note':'Observed tokens and request counts; no currency estimate without verified billing.'})
    lines=['# 四城三组对照报告','','所有失败/超时保留在分母。主观分来自逐次 Codex 盲评；缺评分不算质量通过。','','| 城市 | 组 | 已生成/36 | 已评 | 均分 | 通过/36 | 硬违规试次 | 延迟中位秒 |','|---|---|---:|---:|---:|---:|---:|---:|']
    for x in stats:lines.append(f"| {x['city']} | {x['variant']} | {x['generated']} | {x['reviewed']} | {x['mean_score']} | {x['passed']} | {x['hard_violation_trials']} | {x['latency_median']} |")
    lines+=['','## 配对改善（按案例聚类的95% bootstrap区间）','','| 城市 | 对比 | 案例数 | 均分差 | 区间 | 通过率差 |','|---|---|---:|---:|---|---:|']
    for x in paired:lines.append(f"| {x['city']} | {x['comparison']} | {x['paired_cases']} | {x['score_delta']} | {x['score_ci95_case_bootstrap']} | {x['pass_rate_delta']} |")
    lines+=['','## 默认切换门槛','',f'达标：{gate}；全部456份评阅完成：{complete}。','',json_dumps(checks),'','详细逐次数据见 comparison.json；匿名评分见 blind_reviews/，原始记录见 runs/。切换配置另有明确记录，本脚本不自动修改生产默认。']
    (out/'COMPARISON.md').write_text('\n'.join(lines)+'\n')
    comparison=read(out/'comparison.json');comparison['all_infrastructure_sensitivity']=combined_sensitivity;comparison['cache_segments']=cache_segments
    write(out/'comparison.json',comparison)
    print('Comparison written; reviewed',sum(r['review_status']=='reviewed' for r in rows),'/',len(rows),'gate=',gate)


def json_dumps(value):
    import json
    return '```json\n'+json.dumps(value,ensure_ascii=False,indent=2)+'\n```'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['init','run','packets','freeze-reviews','compare']);parser.add_argument('--out',type=Path,default=ROOT/'artifacts/travel-benchmark/four-city-v2');args=parser.parse_args();out=args.out.resolve()
    {'init':initialize,'run':run,'packets':packets,'freeze-reviews':freeze_reviews,'compare':compare}[args.command](out)

if __name__=='__main__':main()
