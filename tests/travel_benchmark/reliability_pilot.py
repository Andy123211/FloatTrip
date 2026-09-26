"""C1/C2 pilot: frozen runtimes, quota-bounded execution, human-authored blind reviews."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys

from .common import ROOT, read, write, digest, now
from .evidence import Catalog, evaluate, quality_verdict
from .experiment import packets, interval
from .budget import used

SEED=20260921
DEFAULT=ROOT/'artifacts/travel-benchmark/c2-reliability-v1'
SCENARIOS={'first-5d','history','senior','late','must-avoid','memory'}


def network_limit(out, exp=None):
    """Quota extensions are explicit events, separate from frozen experiment data."""
    limit = (exp or read(out/'experiment.json'))['network_limit']
    for path in sorted((out/'quota_extensions').glob('*.json')):
        approval = read(path)
        if approval.get('previous_limit') != limit or approval.get('additional_requests', 0) <= 0 or not approval.get('explicit_user_authorization'):
            raise SystemExit('Invalid quota extension: ' + str(path))
        limit += approval['additional_requests']
    return limit


def archive_budget_interruption(base, key):
    """Keep the incomplete attempt byte-for-byte; never retry algorithm failures."""
    source = base/'trials'/f'{key}.json'
    if read(source).get('status') != 'budget_interrupted':
        raise ValueError('Only a budget interruption can be resumed')
    parent = base/'attempts'/key
    number = 1 + len(list(parent.glob('attempt-*')))
    dest = parent/f'attempt-{number:03}'
    dest.mkdir(parents=True)
    saved = {}
    files = [(source, 'trial.json'), (base/'logs'/f'{key}.json', 'log.json'),
             (base/'evidence'/f'{key}.json', 'evidence.json')]
    files += [(p, p.name) for p in source.parent.glob(key+'.sqlite*')]
    for path, name in files:
        if path.exists():
            saved[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            path.rename(dest/name)
    write(dest/'preservation.json', {'at':now(), 'reason':'Explicit quota extension; retry only quota-interrupted execution',
                                   'files':saved, 'excluded_from_algorithm_failure_denominator':True})
    return str((dest/'trial.json').relative_to(base))


def retained_attempts(base, record):
    return [read(base/name) for name in record.get('prior_attempts', [])] + [record]


def initialize(out, arms=('C1','C2'), network_limit=500, require_quota_authorization=False):
    if (out/'experiment.json').exists():raise SystemExit('Already frozen; use run')
    if not (out/'baseline_manifest.json').exists():raise SystemExit('Pre-C2 snapshot required')
    baseline=read(out/'baseline_manifest.json')
    for name,expected in baseline['files'].items():
        if hashlib.sha256((out/'baseline'/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Baseline drift: '+name)
    source=ROOT/'tests/travel_benchmark/data/four_city_v2'
    shutil.copytree(source,out/'frozen')
    data=read(source/'cases.json')
    data['cases']=[c for c in data['cases'] if c['city'] in ('上海','南京') and c['scenario'] in SCENARIOS]
    assert len(data['cases'])==12
    data['version']=out.name;write(out/'frozen/cases.json',data)
    # Evaluation-only correction: a popularity group is not one visitable entity.
    catalog=read(out/'frozen/catalog.json')
    for item in catalog['attractions']:
        if item['id']=='sh-wukang':
            item['visit_identity_by_alias']={name:('sh-wukang-building' if name=='武康大楼' else 'sh-wukang-road') for name in [item['name'],*item['aliases']]}
        if item['id']=='nj-qinhuai':
            item['visit_identity_by_alias']={name:('nj-qinhuai-river' if name=='秦淮河' else 'nj-dacheng' if name=='夫子庙大成殿' else 'nj-fuzimiao-street' if name=='夫子庙步行街' else 'nj-qinhuai-area') for name in [item['name'],*item['aliases']]}
    write(out/'frozen/catalog.json',catalog)
    write(out/'frozen/evaluator_corrections.json',{'version':'visit-identity-v1','applies_to':list(arms),
        'unchanged':['requests','dates','rubric','popular_group_targets','popular_group_counts'],
        'correction':'Distinct destinations within one reference popularity group are not automatically duplicate hard violations. Repeated coverage remains grouped and visit redundancy is manually reviewed.',
        'sources':[{'url':'https://www.shanghai.gov.cn/citywalk/20260625/3492b1b945c146fc96c4585d7b04f00d.html','retrieved_on':'2026-09-21','evidence':'Official district description distinguishes Wukang building from surrounding street district.'},
                   {'url':'https://www.nanjing.gov.cn/zzb/hdhy/hygq_72666/202208/t20220829_3684096.html','retrieved_on':'2026-09-21','evidence':'Official scenic-area composition identifies multiple distinct attractions, including Dacheng Hall.'},
                   {'url':'https://njfzm.net/brc/40.htm','retrieved_on':'2026-09-21','evidence':'Scenic-area introduction distinguishes river tour from attractions along its banks.'}]})
    schedule=[];rng=random.Random(SEED)
    for repeat in range(1,4):
        cases=list(data['cases']);rng.shuffle(cases)
        for index,c in enumerate(cases):
            order=list(arms) if (index+repeat)%2 else list(reversed(arms))
            for arm in order:schedule.append({'case_id':c['id'],'city':c['city'],'mode':'planning','trial':repeat,'variant':arm})
    for c in data['cases']:
        if c['dialogue']:
            order=list(arms);rng.shuffle(order)
            for arm in order:schedule.append({'case_id':c['id'],'city':c['city'],'mode':'dialogue','trial':1,'variant':arm})
    assert len(schedule)==80
    write(out/'schedule.json',schedule)
    from app.core.env import load_local_env
    load_local_env()
    for arm in arms:
        runtime=out/'runtimes'/arm;runtime.mkdir(parents=True)
        src=out/'baseline' if arm==arms[0] else ROOT
        shutil.copytree(src/'app',runtime/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (runtime/'tests').mkdir();(runtime/'tests/__init__.py').write_text('')
        shutil.copytree(ROOT/'tests/travel_benchmark',runtime/'tests/travel_benchmark',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        run=out/'runs'/arm;run.mkdir(parents=True);shutil.copytree(out/'frozen',run/'frozen')
        env=dict(os.environ,PLANNING_VARIANT='C')
        manifest=json.loads(subprocess.check_output([sys.executable,'-c','import json;from tests.travel_benchmark.common import provenance;print(json.dumps(provenance()))'],cwd=runtime,env=env,text=True))
        manifest.update(planning_variant='C',benchmark_hash=digest({p.name:read(p) for p in (out/'frozen').glob('*.json')}))
        write(run/'manifest.json',manifest)
        write(run/'schedule.json',[{k:v for k,v in s.items() if k in ('case_id','mode','trial')} for s in schedule if s['variant']==arm])
    from .experiment import initialize_cache_policy
    initialize_cache_policy(out)
    policy=read(out/'cache_policy_v1/policy.json')
    policy.update(network_request_limit=network_limit,ledger='budget.sqlite',counting='Atomic reservation before each network attempt; exceptions count; cache hits do not count')
    write(out/'cache_policy_v1/policy.json',policy)
    from app.core.amap_cache import AmapCache
    from scripts.seed_amap_cache import seed
    sources = sorted((ROOT/'artifacts/travel-benchmark/four-city-v2/runs').glob('*/trials/*.json'))
    if tuple(arms) != ('C1','C2'):
        sources += sorted((DEFAULT/'runs').glob('*/trials/*.json'))
    seeded=seed(sources,AmapCache(out/'cache_policy_v1/responses.sqlite'))
    write(out/'cache_policy_v1/seed_manifest.json',{'created_at':now(),'counts':seeded,'original_timestamps_preserved':True})
    write(out/'experiment.json',{'created_at':now(),'count':80,'seed':SEED,'default_strategy':'A',
        'network_limit':network_limit,'date_policy':'Original case dates retained','arms':list(arms),
        'requires_quota_authorization':require_quota_authorization,
        'gate':{'main_per_city':18,'minimum_passed':15,'confirmed_hard_violations':0,'nonregression':['mean_score','pass_rate','generation_rate'],'at_least_one_quality_metric_improves':True},
        'frozen_hashes':{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (out/'frozen').iterdir() if p.is_file()},
        'dependencies':subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines()})
    print('Frozen 80-trial C1/C2 pilot; seeded',seeded)


def run(out, max_trials=None):
    from app.core.env import load_local_env
    load_local_env(); exp=read(out/'experiment.json')
    limit = network_limit(out, exp)
    if exp.get('requires_quota_authorization'):
        approval = out/'quota_authorization.json'
        if not approval.exists() or read(approval).get('approved_network_limit',0) < exp['network_limit']:
            raise SystemExit('Additional Amap quota authorization pending; no trial dispatched')
    for name,value in exp['frozen_hashes'].items():
        if hashlib.sha256((out/name).read_bytes()).hexdigest()!=value:raise SystemExit('Frozen data drift')
    if subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines()!=exp['dependencies']:raise SystemExit('Dependency drift')
    cases={c['id']:c for c in read(out/'frozen/cases.json')['cases']};cat=Catalog(read(out/'frozen/catalog.json'))
    ledger=out/'budget.sqlite'; dispatched=0
    for index,s in enumerate(read(out/'schedule.json')):
        base=out/'runs'/s['variant'];key=f'{s["case_id"]}.{s["mode"]}.{s["trial"]}';path=base/'trials'/f'{key}.json'
        if path.exists() and read(path).get('status') in ('succeeded','failed','timeout','interrupted'):continue
        if used(ledger)>=limit:
            write(out/'dispatch_status.json',{'status':'budget_paused','next_index':index+1,'network_attempts':used(ledger),'at':now()});break
        if max_trials is not None and dispatched>=max_trials:break
        if path.exists() and read(path).get('status') == 'budget_interrupted':
            archive_budget_interruption(base, key)
        if path.exists():
            r=read(path);r.update(status='interrupted',finished_at=now());write(path,r)
        else:
            env=dict(os.environ,PLANNING_VARIANT='C',BENCHMARK_CACHE_NAMESPACE=f'pilot:{out.name}:{s["variant"]}:{key}',
                AMAP_CACHE_ENABLED='1',AMAP_CACHE_PATH=str(out/'cache_policy_v1/responses.sqlite'),
                BENCHMARK_BUDGET_DB=str(ledger),BENCHMARK_NETWORK_LIMIT=str(limit),BENCHMARK_TRIAL=s['variant']+':'+key)
            cmd=[sys.executable,str(out/'cache_policy_v1/cache_worker.py'),'worker','--out',str(base),'--case',s['case_id'],'--mode',s['mode'],'--trial',str(s['trial'])]
            print(f'{index+1}/80 RUN {s["variant"]} {key} network={used(ledger)}',flush=True)
            try:
                proc=subprocess.run(cmd,cwd=out/'runtimes'/s['variant'],env=env,capture_output=True,text=True,timeout=600)
                write(base/'logs'/f'{key}.json',{'returncode':proc.returncode,'stdout':proc.stdout[-15000:],'stderr':proc.stderr[-15000:]})
                r=read(path) if path.exists() else {**s,'state':{},'status':'running'}
                if 'AMAP_NETWORK_BUDGET_EXHAUSTED' in proc.stderr:
                    r.update(status='budget_interrupted',error={'type':'BudgetExhausted','message':'Stopped before next request; incomplete, not an algorithm failure'})
                elif r['status']=='running':r.update(status='failed',error={'type':'WorkerFailure','message':f'exit {proc.returncode}'})
            except subprocess.TimeoutExpired:
                r=read(path) if path.exists() else {**s,'state':{}}
                r.update(status='timeout',elapsed_seconds=600,error={'type':'TimeoutError','message':'600 second limit'})
            r['prior_attempts'] = [str(p.relative_to(base)) for p in sorted((base/'attempts'/key).glob('attempt-*/trial.json'))]
            write(path,r);dispatched+=1
        write(base/'evidence'/f'{key}.json',evaluate(r,cases[s['case_id']],cat))
        print(f'{index+1}/80 {r["status"]} network={used(ledger)}',flush=True)
    else:write(out/'dispatch_status.json',{'status':'completed','network_attempts':used(ledger),'at':now()})
    packets(out)


def freeze_reviews(out):
    paths=sorted((out/'blind_packets').glob('review-*.json'));hashes={}
    for p in paths:
        packet=read(p)
        if packet['status']=='budget_interrupted':continue
        rp=out/'blind_reviews'/p.name
        if not rp.exists():raise SystemExit('Missing review: '+p.name)
        review=read(rp)
        if not review.get('blind_to_variant') or not review.get('blind_to_internal_objective'):raise SystemExit('Unblinded review')
        quality_verdict(review,packet['evidence'],packet['status'])
        if any(x*2!=int(x*2) for x in review['scores'].values()):raise SystemExit('Invalid score increments')
        if not all(review.get(k) for k in ('reviewer','reviewed_at','reasons','confidence','evidence_references')):raise SystemExit('Missing review evidence')
        for f in (p,rp):hashes[str(f.relative_to(out))]=digest(read(f))
    count=len(hashes)//2
    for extra in [out/'supplemental_review_sources.json',out/'cache_policy_v1/policy.json',
                  *(out/'review_revisions').glob('**/*.json')]:
        if extra.exists():hashes[str(extra.relative_to(out))]=digest(read(extra))
    dest=out/'reviews_freeze.json'
    previous = None
    if dest.exists() and read(dest)['hashes']!=hashes:
        old = read(dest)
        if not old.get('partial') or any(hashes.get(k) != v for k,v in old['hashes'].items()):
            raise SystemExit('Review seal changed')
        previous = digest(old)
        write(out/'review_seal_history'/f'{previous}.json', old)
    if not dest.exists() or previous:
        write(dest,{'at':now(),'count':count,'hashes':hashes,'partial':count<80,'previous_seal':previous})


def compare(out):
    seal=read(out/'reviews_freeze.json')
    for name,value in seal['hashes'].items():
        if digest(read(out/name))!=value:raise SystemExit('Review seal drift: '+name)
    arms=read(out/'experiment.json').get('arms',['C1','C2'])
    old_arm,new_arm=arms
    mapping=read(out/'blind_mapping.json');rows=[]
    for uid,s in mapping.items():
        packet=read(out/'blind_packets'/f'{uid}.json')
        if packet['status']=='budget_interrupted':continue
        review=read(out/'blind_reviews'/f'{uid}.json');r=read(out/'runs'/s['variant']/'trials'/f'{s["key"]}.json')
        attempts = retained_attempts(out/'runs'/s['variant'], r)
        v=quality_verdict(review,packet['evidence'],r['status'])
        pending=(r.get('state',{}).get('final_plan') or {}).get('pending_checks')
        rows.append({**s,**v,'review_id':uid,'status':r['status'],'scores':review['scores'],
            'hard_violations':packet['evidence']['hard_violations']+review.get('additional_hard_violations',[]),
            'elapsed_seconds':r.get('elapsed_seconds'),'pending_checks':pending,
            'coverage':packet['evidence']['coverage'],'model_usage':r.get('model_usage',[]),
            'prior_budget_interruptions':len(attempts)-1,
            'all_attempts_elapsed_seconds':sum(a.get('elapsed_seconds',0) for a in attempts),
            'cache_hits':sum(e.get('cache_hit',False) for a in attempts for e in a.get('amap_request_cache',[])),
            'network_responses':sum(not e.get('cache_hit',False) for a in attempts for e in a.get('amap_request_cache',[]))})
    stats=[];pairs=[]
    avg=lambda xs:statistics.mean(xs) if xs else None
    for city in ('上海','南京'):
        for arm in arms:
            rr=[r for r in rows if r['city']==city and r['variant']==arm and r['mode']=='planning']
            stats.append({'city':city,'variant':arm,'planned':18,'completed':len(rr),'mean_score':avg([r['score'] for r in rr]),
                'passed':sum(r['passed'] for r in rr),'pass_rate_completed':avg([r['passed'] for r in rr]),
                'generated':sum(r['status']=='succeeded' for r in rr),'generation_rate':avg([r['status']=='succeeded' for r in rr]),
                'hard_violation_trials':sum(bool(r['hard_violations']) for r in rr),
                'latency_median':avg([]) if not rr else statistics.median([r['elapsed_seconds'] for r in rr if r['elapsed_seconds'] is not None]),
                'dimensions':{k:avg([r['scores'][k] for r in rr]) for k in ('popularity','personalization','route','validity')},
                'pending_output_count':sum(bool(r['pending_checks']) for r in rr),
                'pending_field_missing':sum(r['pending_checks'] is None for r in rr),
                'network_responses':sum(r['network_responses'] for r in rr),'cache_hits':sum(r['cache_hits'] for r in rr)})
        by_case=defaultdict(dict)
        for r in rows:
            if r['city']==city and r['mode']=='planning':by_case[r['case_id']][(r['variant'],r['trial'])]=r
        deltas=[];pass_deltas=[];matched=0
        for block in by_case.values():
            trials=[t for t in (1,2,3) if (old_arm,t) in block and (new_arm,t) in block]
            if trials:
                deltas.append(avg([block[new_arm,t]['score']-block[old_arm,t]['score'] for t in trials]))
                pass_deltas.append(avg([int(block[new_arm,t]['passed'])-int(block[old_arm,t]['passed']) for t in trials]));matched+=len(trials)
        pairs.append({'city':city,'matched_repeats':matched,'case_count':len(deltas),'score_delta':avg(deltas),'score_ci95':interval(deltas),
            'pass_delta':avg(pass_deltas),'pass_ci95':interval(pass_deltas),'partial_pairs':matched<18})
    complete=len(rows)==80
    gate=complete and not any(r['hard_violations'] for r in rows if r['variant']==new_arm)
    for city in ('上海','南京'):
        old,new=[next(s for s in stats if s['city']==city and s['variant']==arm) for arm in arms]
        gate=gate and new['passed']>=15 and all(new[k]>=old[k] for k in ('mean_score','passed','generated')) and (new['mean_score']>old['mean_score'] or new['passed']>old['passed'])
    result={'at':now(),'planned':80,'reviewed':len(rows),'complete':complete,'stats':stats,'paired':pairs,'trials':rows,
        'network_attempts':used(out/'budget.sqlite'),'network_limit':network_limit(out),'default_strategy':'A','expand_validation':bool(gate),
        'decision':'eligible_for_expanded_validation' if gate else 'insufficient_evidence' if not complete else 'gate_not_met',
        'limitations':['Use common evidence for verification completeness; missing pending fields are not zero unknowns, and partial calendars retain unresolved exceptions.',
            'Partial comparisons use only completed matched repeats. Unrun and budget-interrupted trials are not algorithm failures. No rollout allowed.']}
    write(out/'comparison.json',result);write(out/'ROLLOUT_DECISION.json',{k:result[k] for k in ('default_strategy','expand_validation','decision','complete','reviewed')})
    lines=[f'# {old_arm}/{new_arm} 可靠性试验','',f'已评 {len(rows)}/80；高德实际请求预算占用 {result["network_attempts"]}/{result["network_limit"]}；默认保持 A。','',
        '| 城市 | 版本 | 完成/18 | 均分 | 通过 | 生成 | 硬违规 |','|---|---|---:|---:|---:|---:|---:|']
    for s in stats:lines.append(f'| {s["city"]} | {s["variant"]} | {s["completed"]} | {s["mean_score"]} | {s["passed"]} | {s["generated"]} | {s["hard_violation_trials"]} |')
    lines+=['','未执行与预算中断不作为算法失败；不完整实验不能通过扩大验证门槛。',f'决定：{result["decision"]}。','',
        '配对差值按案例聚类，仅使用两版本均完成的重复；详细置信区间、原始分项、待核实项及用量见 comparison.json。']
    (out/'COMPARISON.md').write_text('\n'.join(lines)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['initialize','run','packets','freeze-reviews','compare']);p.add_argument('--out',type=Path,default=DEFAULT);p.add_argument('--max-trials',type=int)
    p.add_argument('--arms',nargs=2,default=['C1','C2']);p.add_argument('--network-limit',type=int,default=500)
    p.add_argument('--require-quota-authorization',action='store_true')
    a=p.parse_args();out=a.out.resolve()
    if a.command=='run':run(out,a.max_trials)
    elif a.command=='initialize':initialize(out,tuple(a.arms),a.network_limit,a.require_quota_authorization)
    else:{'packets':packets,'freeze-reviews':freeze_reviews,'compare':compare}[a.command](out)


if __name__=='__main__':main()
