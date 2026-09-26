"""Prepare anonymous development replay packets; subjective scores are authored by Codex."""
from __future__ import annotations
import argparse
import random
import statistics
from collections import defaultdict
from .common import read, write, digest, now
from .reliability_iterations import OUT, SOURCE
from .evidence import quality_verdict

BASE = OUT
VERSIONS = ('C3', 'C4')


def packets():
    cases = {c['id']: c for c in read(SOURCE / 'frozen/cases.json')['cases']}
    rows = [(arm, p) for arm in VERSIONS for p in sorted((BASE / arm / 'trials').glob('*.json'))]
    if len(rows) != 80:
        raise SystemExit('Both full 40-input replays required')
    if (OUT / 'reviews_freeze.json').exists():
        raise SystemExit('Reviews sealed')
    random.Random(20260923).shuffle(rows)
    mapping = {}
    for index, (arm, path) in enumerate(rows, 1):
        uid = f'review-{index:03}'
        raw = read(path); case = cases[raw['case_id']]
        evidence = read(BASE / arm / 'evidence' / path.name)
        state = raw['state']; plan = state.get('final_plan') or {}
        mapping[uid] = {'variant': arm, 'key': path.stem, 'case_id': raw['case_id'], 'trial': raw['trial'], 'mode': raw['mode'], 'city': case['city']}
        packet = {'id':uid, 'city':case['city'], 'request':case['input']['query'], 'status':raw['status'],
            'mode':'downstream_replay', 'input_origin':raw['mode'], 'expectations':case['expectations'],
            'evidence':{k:v for k,v in evidence.items() if k not in ('stage_names','missed_hot')},
            'output_details':[{'day':d['day'], **{k:s.get(k) for k in ('name','open_time','address')}}
                for d in plan.get('days',[]) for s in d['timeline'] if s.get('type')=='attraction'],
            'transport_evidence':[{k:e.get(k) for k in ('from','to','mode','status','duration_minutes','buffer_minutes')}
                                  for e in state.get('transport_evidence',[])],
            'failure_type':(raw.get('error') or {}).get('type'),
            'limits':'Archived candidate/transport replay; no fresh data, new model proposal, or dialogue. Do not grade as a new conversational acceptance.'}
        dest = OUT / 'blind_packets' / f'{uid}.json'
        if dest.exists() and read(dest) != packet:
            raise SystemExit('Packet drift')
        write(dest, packet)
    write(OUT / 'blind_mapping.json', mapping)
    print('Prepared 80 anonymized replay packets; subjective review pending')


def seal():
    hashes = {}
    for packet_path in sorted((OUT / 'blind_packets').glob('*.json')):
        packet = read(packet_path)
        review_path = OUT / 'blind_reviews' / packet_path.name
        review = read(review_path)
        assert review['reviewer']=='Codex' and review['blind_to_internal_objective'] and review['blind_to_variant']
        assert all(review['reasons'].get(k) for k in ('popularity','personalization','route','validity'))
        quality_verdict(review, packet['evidence'], packet['status'])
        for path in (packet_path, review_path):
            hashes[str(path.relative_to(OUT))] = digest(read(path))
    assert len(hashes) == 160
    dest = OUT / 'reviews_freeze.json'
    if dest.exists():
        assert read(dest)['hashes'] == hashes
    else:
        write(dest, {'at':now(), 'hashes':hashes,'count':80, 'scope':'Development downstream replay, not online acceptance'})


def compare():
    seal_data = read(OUT / 'reviews_freeze.json')
    assert all(digest(read(OUT / p))==v for p,v in seal_data['hashes'].items())
    rows = []
    for uid, item in read(OUT / 'blind_mapping.json').items():
        p = read(OUT / 'blind_packets' / f'{uid}.json')
        r = read(OUT / 'blind_reviews' / f'{uid}.json')
        rows.append({**item,'id':uid,**quality_verdict(r,p['evidence'],p['status']),
            'hard_violations':p['evidence']['hard_violations']+r.get('additional_hard_violations',[]),
            'scores':r['scores'],'status':p['status']})
    summaries = []
    for city in ('上海','南京'):
        for arm in VERSIONS:
            selected = [r for r in rows if r['city']==city and r['variant']==arm and r['mode']=='planning']
            summaries.append({'city':city,'variant':arm,'trials':len(selected),
                'mean_score':statistics.mean(r['score'] for r in selected), 'passed':sum(r['passed'] for r in selected),
                'hard_violation_trials':sum(bool(r['hard_violations']) for r in selected)})
    write(OUT / 'comparison.json', {'at':now(),'scope':'development downstream replay only',
        'online_goal_verified':False,'stats':summaries,'trials':rows})
    print(summaries)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['packets','seal','compare'])
    parser.add_argument('--variants', nargs=2, default=['C3','C4'])
    args=parser.parse_args(); VERSIONS=tuple(args.variants)
    if VERSIONS != ('C3','C4'):
        OUT=BASE/('reviews-'+'-'.join(VERSIONS))
    {'packets':packets,'seal':seal,'compare':compare}[args.command]()
