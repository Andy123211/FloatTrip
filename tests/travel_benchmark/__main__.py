from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from .common import DATA, ROOT, digest, now, provenance, read, write
from .evidence import Catalog, evaluate


def main():
    ap=argparse.ArgumentParser(description='Shanghai/Nanjing independent travel benchmark')
    ap.add_argument('command',choices=['run','replay','report','worker','compare'])
    ap.add_argument('--out',type=Path,default=ROOT/'artifacts/travel-benchmark/2026-09-19')
    ap.add_argument('--city',choices=['上海','南京','北京','重庆'])
    ap.add_argument('--variant',choices=['A','B','C'],default=None)
    ap.add_argument('--data',type=Path,default=DATA)
    ap.add_argument('--case',dest='case_id')
    ap.add_argument('--k',type=int,default=3)
    ap.add_argument('--mode',choices=['planning','dialogue'],default='planning')
    ap.add_argument('--trial',type=int,default=1)
    ap.add_argument('--smoke',action='store_true')
    ap.add_argument('--refresh-evidence',action='store_true',help='Replay after a documented evaluator correction; archive previous evidence')
    ap.add_argument('--optimizer',action='store_true',help='Also replay production optimizer on the saved final candidate pool, offline')
    args=ap.parse_args()
    args.out=args.out.resolve()
    if args.command=='compare':
        from .experiment import compare
        compare(args.out)
        return
    if args.variant:
        os.environ['PLANNING_VARIANT']=args.variant
    if args.k<1:
        ap.error('--k must be positive')
    if args.command=='run':
        args.out.mkdir(parents=True,exist_ok=True)
        manifest_path=args.out/'manifest.json'
        if manifest_path.exists():
            manifest=read(manifest_path)
            if provenance()['source_fingerprint']!=manifest['source_fingerprint']:
                raise SystemExit('Source/data/rubric changed. Use a new output directory; refusing mixed baseline.')
        else:
            manifest=provenance()
            manifest['benchmark_hash']=digest({p.name:read(p) for p in sorted(args.data.glob('*.json'))})
            manifest['planning_variant']=os.getenv('PLANNING_VARIANT','A')
            write(manifest_path,manifest)
            for p in args.data.glob('*.json'):
                write(args.out/'frozen'/p.name,read(p))
            (args.out/'frozen'/'RUBRIC.md').write_text(Path(__file__).with_name('RUBRIC.md').read_text())
    else:
        manifest=read(args.out/'manifest.json')
    cases=read(args.out/'frozen/cases.json')['cases']
    if args.city:cases=[c for c in cases if c['city']==args.city]
    if args.case_id:cases=[c for c in cases if c['id']==args.case_id]
    if args.mode=='dialogue':cases=[c for c in cases if c['dialogue']]
    if args.smoke:cases=[c for c in cases if c['scenario']=='first-1d']
    if not cases:raise SystemExit('No matching cases')
    adjudication_path=args.out/'identity_adjudications.json'
    adjudications=read(adjudication_path) if adjudication_path.exists() else None
    cat=Catalog(read(args.out/'frozen/catalog.json'),adjudications)
    if args.command=='worker':
        from .runner import execute
        case=cases[0]
        path=args.out/'trials'/f'{case["id"]}.{args.mode}.{args.trial}.json'
        asyncio.run(execute(case,path,args.trial,args.mode,manifest))
        return
    if args.command=='run':
        schedule_path=args.out/'schedule.json'
        schedule=read(schedule_path) if schedule_path.exists() else []
        count=1 if args.mode=='dialogue' or args.smoke else args.k
        additions=[{'case_id':c['id'],'mode':args.mode,'trial':i} for c in cases for i in range(1,count+1)]
        schedule=list({(s['case_id'],s['mode'],s['trial']):s for s in schedule+additions}.values())
        write(schedule_path,schedule)
        for case in cases:
            for trial in range(1,count+1):
                key=f'{case["id"]}.{args.mode}.{trial}'
                path=args.out/'trials'/f'{key}.json'
                if path.exists() and read(path).get('status') in ['succeeded','failed','timeout','interrupted']:
                    print(f'SKIP {key}',flush=True)
                    continue
                if path.exists():
                    old=read(path);old['status']='interrupted';old['finished_at']=now()
                    write(path,old)
                    print(f'INTERRUPTED retained {key}; new experiment required for retry',flush=True)
                    continue
                cmd=[sys.executable,'-m','tests.travel_benchmark','worker','--out',str(args.out),'--case',case['id'],'--mode',args.mode,'--trial',str(trial)]
                print(f'RUN {key}',flush=True)
                try:
                    result=subprocess.run(cmd,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=600)
                    # Persist only sanitized logs, never raw provider diagnostics.
                    from app.core.env import load_local_env
                    load_local_env()
                    write(args.out/'logs'/f'{key}.json',{'returncode':result.returncode,'log':result.stdout[-20000:]})
                    if not path.exists():
                        write(path,{'case_id':case['id'],'mode':args.mode,'trial':trial,'status':'failed','error':{'type':'WorkerFailure','message':f'exit {result.returncode}'},'state':{}})
                    elif read(path)['status']=='running':
                        r=read(path);r.update(status='failed',error={'type':'WorkerFailure','message':f'exit {result.returncode}'})
                        write(path,r)
                except subprocess.TimeoutExpired:
                    r=read(path) if path.exists() else {'case_id':case['id'],'mode':args.mode,'trial':trial,'state':{}}
                    r.update(status='timeout',finished_at=now(),elapsed_seconds=600,error={'type':'TimeoutError','message':'Trial process killed at 600 seconds'})
                    write(path,r)
                r=read(path)
                ev=evaluate(r,case,cat)
                write(args.out/'evidence'/f'{key}.json',ev)
                # Blind packet excludes solver objective and candidate self-scores.
                write(args.out/'review_packets'/f'{key}.json',{'request':case['input']['query'],'expectations':case['expectations'],'evidence':{k:v for k,v in ev.items() if k!='missed_hot'},'error':r.get('error')})
                print(f'{r["status"].upper()} {key} {r.get("elapsed_seconds")}s hard={len(ev["hard_violations"])}',flush=True)
    elif args.command=='replay':
        n=0
        by_id={c['id']:c for c in cases}
        revision=now().replace(':','-')
        changed=[]
        for p in sorted((args.out/'trials').glob('*.json')):
            r=read(p)
            if r['case_id'] not in by_id:continue
            ev=evaluate(r,by_id[r['case_id']],cat)
            previous=args.out/'evidence'/p.name
            if previous.exists() and read(previous)!=ev:
                if not args.refresh_evidence:
                    raise SystemExit(f'Evidence mismatch: {p.name}; use --refresh-evidence only for a documented evaluator correction')
                write(args.out/'evidence_revisions'/revision/p.name,read(previous))
                changed.append(p.name)
            write(previous,ev);n+=1
            if args.optimizer and r.get('status')=='succeeded' and r.get('state',{}).get('candidate_pool'):
                from .replay import replay_optimizer
                write(args.out/'optimizer_replays'/p.name,asyncio.run(replay_optimizer(r)))
        if changed:
            write(args.out/'evidence_revisions'/revision/'revision.json',{
                'created_at':now(),'changed_trials':changed,'adjudications_hash':digest(adjudications),
                'evaluator_provenance':provenance(),
                'reason':'Source-backed identity aliases; avoid scope applies to named entities, not popularity parent groups; unknown POIs do not share a group. Frozen requests, rubric, model outputs and Codex scores unchanged.'})
        print(f'Offline evidence replay matched {n} trials; no network or model calls.')
    if args.command in ['run','report']:
        from .report import report
        report(args.out)


if __name__=='__main__':main()
