"""Offline integrity audit; never computes or reveals treatment quality scores."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from .common import ROOT, digest, now, read, write
from .evidence import Catalog, evaluate


def audit(out: Path):
    cases={c['id']:c for c in read(out/'frozen/cases.json')['cases']}
    catalog=Catalog(read(out/'frozen/catalog.json'))
    findings=[]; completed=0; replayed=0; verified_snapshots=0; isolated_dialogues=0
    source_files=0; cache_reads=0; cache_hits=0; api_response_hashes=[]; search_responses={}
    local_hits=0; network_calls=0; cache_segments={}
    policy_path=out/'cache_policy_v1/policy.json'
    policy=read(policy_path) if policy_path.exists() else None
    if policy:
        for name,expected in policy['code_hashes'].items():
            if hashlib.sha256((policy_path.parent/name).read_bytes()).hexdigest()!=expected:
                findings.append({'kind':'cache_overlay_drift','file':name})
    baseline=read(out/'baseline_manifest.json')
    for name,expected in baseline['source_hashes'].items():
        path=out/'baseline'/name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            findings.append({'kind':'baseline_drift','file':name})
        source_files+=1
    for arm in ['A','B','C']:
        base=out/'runs'/arm; manifest=read(base/'manifest.json')
        for name,expected in manifest['source_hashes'].items():
            path=out/'runtimes'/arm/name
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                findings.append({'kind':'runtime_drift','variant':arm,'file':name})
            source_files+=1
        for path in sorted((base/'trials').glob('*.json')):
            record=read(path)
            if record['status']=='running':continue
            completed+=1;case=cases[record['case_id']]
            segment=record.get('cache_policy','per-trial-cold-v2')
            cache_segments[segment]=cache_segments.get(segment,0)+1
            if record.get('cache_policy') and (not policy or record.get('cache_overlay_hash')!=digest(policy)):
                findings.append({'kind':'cache_policy_fingerprint_mismatch','trial':path.name})
            for key in ['source_changes_before','source_changes_after']:
                if record.get(key):findings.append({'kind':'trial_source_drift','trial':path.name,'field':key})
            if record.get('source_fingerprint')!=manifest['source_fingerprint']:
                findings.append({'kind':'fingerprint_mismatch','trial':path.name})
            snapshot=record.get('submitted_snapshot')
            if record.get('mode')=='planning' and snapshot is not None:
                if snapshot!=case['input']:findings.append({'kind':'planning_input_mismatch','trial':path.name})
                else:verified_snapshots+=1
            if snapshot is not None and any(k in snapshot for k in ['catalog','expectations','eligible_hot','hot_group_target']):
                findings.append({'kind':'benchmark_answer_leak','trial':path.name})
            if record.get('mode')=='dialogue':
                if path.with_suffix('.sqlite').exists():isolated_dialogues+=1
                else:findings.append({'kind':'missing_dialogue_database','trial':path.name})
            prior=base/'evidence'/path.name
            if prior.exists():
                if evaluate(record,case,catalog)!=read(prior):findings.append({'kind':'objective_replay_mismatch','trial':path.name})
                else:replayed+=1
            else:findings.append({'kind':'missing_objective_evidence','trial':path.name})
            cache_reads+=len(record.get('cache_reads',[]))
            cache_hits+=sum(bool(x['hit']) for x in record.get('cache_reads',[]))
            for call in record.get('search',[]):
                if call.get('response_hash'):
                    request={'city':call['city'],'parameters':call['parameters']}
                    entry=search_responses.setdefault(digest(request),{'request':request,'responses':[]})
                    entry['responses'].append({'record':str(path.relative_to(out)),'response_hash':call['response_hash']})
            for index,call in enumerate(record.get('api_calls',[])):
                api_response_hashes.append({'record':str(path.relative_to(out)),'call':index,'hash':digest(call.get('response'))})
                local_hits+=bool(call.get('cache_hit'))
                network_calls+=not bool(call.get('cache_hit'))
            events=record.get('amap_request_cache',[])
            if events:
                from app.core.amap_cache import response_hash
                if len(events)!=len(record.get('api_calls',[])):
                    findings.append({'kind':'cache_event_count_mismatch','trial':path.name})
                for call,event in zip(record.get('api_calls',[]),events):
                    if event.get('response_hash') and response_hash(call['response'])!=event['response_hash']:
                        findings.append({'kind':'cache_response_hash_mismatch','trial':path.name})
    old_dependencies=(out/'dependencies.txt').read_bytes()
    current_dependencies=subprocess.check_output([sys.executable,'-m','pip','freeze','--disable-pip-version-check'],stderr=subprocess.DEVNULL)
    dependency_check={'initial_sha256':hashlib.sha256(old_dependencies).hexdigest(),'current_sha256':hashlib.sha256(current_dependencies).hexdigest(),'unchanged':old_dependencies==current_dependencies}
    if not dependency_check['unchanged']:findings.append({'kind':'dependency_drift'})
    result={'created_at':now(),'scheduled':456,'completed':completed,'objective_replays_matched':replayed,'dependencies':dependency_check,
            'planning_snapshots_match_frozen_public_input':verified_snapshots,'isolated_dialogue_databases':isolated_dialogues,
            'source_files_checked':source_files,'findings':findings,'cache_reads_observed':cache_reads,'cache_hits_observed':cache_hits,
            'api_response_hashes':api_response_hashes,'search_responses_by_request':search_responses,
            'cache_segments':cache_segments,'local_amap_cache_hits':local_hits,'observed_amap_network_responses':network_calls,
            'limits':['Input-key and snapshot equality audits supplement static import isolation; they do not prove absence of every possible semantic leak.',
                      'Model tokens and HTTP requests are observed where instrumentation sees them; failures before response may not be counted.',
                      'Trials 1–415 use the frozen cold-cache policy. From 416, the user-requested versioned HTTP overlay reuses exact successful requests across arms; frozen planning code is unchanged.']}
    write(out/'integrity_audit.json',result)
    print(f'Completed {completed}/456; replay matched {replayed}; source files {source_files}; findings {len(findings)}')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=ROOT/'artifacts/travel-benchmark/four-city-v2')
    audit(parser.parse_args().out.resolve())
