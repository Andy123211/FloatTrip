"""Export demo evidence, including failed rehearsal; no external calls."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/interview-demo/shanghai-c5-match-v1'
sys.path.insert(0,str(OUT))
from app.planning.grounded import validation
from app.planning.schemas import TravelPlanState


def dump(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def main():
    manifest=json.loads((OUT/'manifest.json').read_text())
    drift=[p for p,h in manifest['files'].items() if hashlib.sha256((OUT/p).read_bytes()).hexdigest()!=h]
    assert not drift,drift
    with sqlite3.connect(f'file:{OUT}/data/app.db?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        records={table:[dict(r) for r in db.execute('SELECT * FROM '+table)]
                 for table in ['runs','messages','planning_briefs','run_events','itineraries']}
    dump('rehearsal_records.json',records)
    checks=[]
    for row in records['itineraries']:
        plan=json.loads(row['plan_json']);raw=json.loads(row['planner_state_json'])
        state=TravelPlanState(**raw)
        delivered=[{'day':d['day'],'spots':[x for x in d['timeline'] if x['type']=='attraction']} for d in plan['days']]
        result=validation(state,plan)
        hard,pending=result['hard_violations'],result['pending_checks']
        names=[x['name'] for d in delivered for x in d['spots']]
        specific={
            'three_days':len(delivered)==3,
            'dates':[d['date'] for d in plan['days']]==['2026-10-13','2026-10-14','2026-10-15'],
            'bund_and_garden':all(n in names for n in ['外滩','上海豫园']),
            'daily_max_two':all(len(d['spots'])<=2 for d in delivered),
            'starts_after_eleven':all(x['start_time']>='11:00' for d in delivered for x in d['spots']),
            'no_duplicate_names':len(names)==len(set(names)),
            'no_theme_park':not any(any(t in n for t in ['游乐场','迪士尼','欢乐谷']) for n in names),
            'honest_pending_status':bool(pending) and plan['validation_summary']['status']=='needs_verification',
        }
        assert all(specific.values()) and not hard,(specific,hard)
        checks.append({'itinerary_id':row['id'],'checks':specific,'hard_violations':hard,'pending_checks':pending,
                       'transport_evidence':raw.get('transport_evidence'),
                       'days':delivered,'plan_sha256':hashlib.sha256(row['plan_json'].encode()).hexdigest()})
    with sqlite3.connect(f'file:{OUT}/data/budget.sqlite?mode=ro',uri=True) as db:
        used=db.execute('SELECT count(*) FROM requests').fetchone()[0]
        hits=db.execute('SELECT count(*) FROM cache_hits').fetchone()[0]
        requests=[{'id':r[0],'at':r[1],'url':r[2],'phase':r[3]} for r in db.execute('SELECT * FROM requests')]
    limit = None if manifest.get('live_network_budget', {}).get('unlimited') else 50
    report={'at':time.time(),'version':manifest['version'],'revision':manifest.get('revision',1),
            'code_drift':drift,'network_used':used,'network_limit':limit,'remaining':None if limit is None else max(0,limit-used),'cache_hits':hits,
            'rehearsals':[{'id':r['id'],'status':r['status'],'error':r['error_internal']} for r in records['runs'] if r['kind']=='chat'],
            'delivered_itineraries':checks,'network_attempts':requests,
            'limitations':['First rehearsal failed before generic POI type disambiguation; preserved, not omitted.',
                'Second rehearsal combined slow-start requirements with must/avoid to validate the repair within two online attempts.',
                'This is a demo acceptance check, not a formal benchmark or general production rollout.',
                'Amap map SDK/tiles are separate browser assets; no browser navigation queries are made.'],
            'formal_experiment_unchanged':True}
    dump('ACCEPTANCE.json',report)
    print(json.dumps({'delivered':len(checks),'hard_violations':sum(len(c['hard_violations']) for c in checks),
                      'network_used':used,'remaining':None if limit is None else max(0,limit-used),'cache_hits':hits},ensure_ascii=False))


if __name__=='__main__':main()
