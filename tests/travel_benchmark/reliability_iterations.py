"""Frozen downstream reliability replays. No model, search, or network calls.

Historical transport fixtures are observations, not fresh or TTL-valid data.
This development experiment isolates calendar/solver/repair changes and cannot
stand in for a fresh end-to-end online or conversational acceptance run.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
from pathlib import Path
import shutil
import time
from unittest.mock import patch

from .common import ROOT, read, write, digest, now
from .evidence import Catalog, evaluate

SOURCE = ROOT / 'artifacts/travel-benchmark/c2-reliability-v1'
OUT = ROOT / 'artifacts/travel-benchmark/reliability-iterations-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(version):
    out = OUT / version
    if out.exists():
        raise SystemExit('Version already exists; do not overwrite a development iteration')
    out.mkdir(parents=True)
    shutil.copytree(ROOT / 'app', out / 'runtime/app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / 'tests/travel_benchmark', out / 'runtime/tests/travel_benchmark', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (out / 'runtime/tests/__init__.py').write_text('')
    files = {str(p.relative_to(out / 'runtime')): sha(p) for p in (out / 'runtime').rglob('*') if p.is_file()}
    inputs = {str(p.relative_to(SOURCE)): sha(p) for p in (SOURCE / 'runs').glob('*/trials/*.json')}
    write(out / 'manifest.json', {'version': version, 'created_at': now(), 'source_hashes': files,
        'source_directory': str(SOURCE), 'input_hashes': inputs, 'method': 'archived_candidate_and_transport_replay',
        'network_allowed': False, 'refresh_historical_timestamps': False,
        'limitations': ['No new LLM drafting, POI search, or real dialogue.',
            'Archived transport fixtures ignore TTL only inside the replay; not current estimates.',
            'Original C2 requirements and candidate semantics retained; inherited extraction errors remain.']})
    print(out)


async def run(version, limit=None):
    from app.planning import grounded, nodes
    from app.planning.reliability import opening_calendar, is_facility, entity_id
    from app.planning.schemas import TravelPlanState
    import httpx
    out = OUT / version
    manifest = read(out / 'manifest.json')
    # Execution must use exactly the frozen app even if the caller is the worktree.
    for name, expected in manifest['source_hashes'].items():
        if name.startswith('app/') and sha(ROOT / name) != expected:
            raise SystemExit('Run in the frozen runtime, or freeze a new version: ' + name)
    source = Path(manifest['source_directory'])
    for name, expected in manifest['input_hashes'].items():
        if sha(source / name) != expected:
            raise SystemExit('Historical input drift: ' + name)
    cases = {c['id']: c for c in read(source / 'frozen/cases.json')['cases']}
    catalog = Catalog(read(source / 'frozen/catalog.json'))
    fixtures = {}
    for name in sorted(manifest['input_hashes']):
        raw = read(source / name)
        for e in raw.get('state', {}).get('transport_evidence', []):
            if e.get('status') != 'estimated' or e.get('duration_minutes') is None:
                continue
            mode = e.get('requested_mode', 'transit')
            key = (e['from'], e['to'], mode)
            entry = {**deepcopy(e), 'archive_record': name, 'archive_record_sha256': manifest['input_hashes'][name],
                'archive_trial_started_at': raw.get('started_at'), 'archive_trial_finished_at': raw.get('finished_at')}
            if key not in fixtures or str(raw.get('finished_at')) > str(fixtures[key].get('archive_trial_finished_at')):
                fixtures[key] = entry

    async def transport(city, left, right, mode):
        entry = fixtures.get((left['name'], right['name'], mode))
        if entry:
            return deepcopy(entry)
        return {'from': left['name'], 'to': right['name'], 'mode': mode, 'status': 'unverified',
                'duration_minutes': None, 'reason': 'not_in_archived_fixture', 'historical_replay': True}

    def archived_edge(evidence, left, right, mode):
        for e in reversed(evidence):
            if (e.get('from_id'), e.get('to_id'), e.get('requested_mode', e.get('mode'))) == (entity_id(left), entity_id(right), mode):
                return e
        return None

    async def forbidden_network(*args, **kwargs):
        raise AssertionError('Network forbidden in archived replay')

    completed = 0
    with patch.object(grounded, 'transport_estimate', transport), patch.object(grounded, 'edge_evidence', archived_edge), patch.object(httpx.AsyncClient, 'request', forbidden_network):
        source_arm = manifest.get('input_variant', 'C2')
        for path in sorted((source / 'runs' / source_arm / 'trials').glob('*.json')):
            if manifest.get('case_ids') and read(path)['case_id'] not in manifest['case_ids']:
                continue
            dest = out / 'trials' / path.name
            if dest.exists():
                continue
            raw = read(path)
            state_data = deepcopy(raw.get('state') or {})
            if not state_data.get('query'):
                # An upstream failure has no candidate state to replay. Preserve
                # that missing input; do not construct a successful new request.
                record = {'case_id': raw['case_id'], 'trial': raw['trial'], 'mode': raw['mode'],
                    'replay_kind': 'downstream_only', 'source_trial': str(path), 'source_sha256': sha(path),
                    'status': 'input_unavailable', 'state': {}, 'network_calls': 0,
                    'source_status': raw.get('status'), 'source_error': raw.get('error'),
                    'error': {'type': 'ArchivedStateUnavailable', 'message': 'No historical state to replay'},
                    'started_at': now(), 'finished_at': now(), 'elapsed_seconds': 0}
                write(dest, record)
                write(out / 'evidence' / path.name, evaluate(record, cases[raw['case_id']], catalog))
                continue
            state_data.update(route=[], final_plan=None, quality_report=None, approved=False,
                              transport_evidence=[], transport_repair_round=0)
            state = TravelPlanState.model_validate(state_data)
            pool = []
            for c in state.candidate_pool:
                c = deepcopy(c)
                if is_facility(c['poi_name']) and not c.get('must_visit'):
                    continue
                c['opening_calendar'] = opening_calendar(c, state.travel_start_date, state.days)
                c['transfer_minutes_to'] = {}
                pool.append(c)
            state = state.model_copy(update={'candidate_pool': pool})
            started = time.perf_counter()
            record = {'case_id': raw['case_id'], 'trial': raw['trial'], 'mode': raw['mode'],
                'replay_kind': 'downstream_only', 'source_trial': str(path), 'source_sha256': sha(path),
                'started_at': now(), 'status': 'running', 'network_calls': 0,
                'stages': [], 'search': raw.get('search', []), 'merged_search': raw.get('merged_search', []),
                'submitted_snapshot': raw.get('submitted_snapshot'), 'historical_transport_not_current': True}
            try:
                result = await grounded.optimize(state)
                state = state.model_copy(update=result)
                state = state.model_copy(update=grounded.quality(state))
                state = state.model_copy(update=nodes.finalize_node(state))
                record['status'] = 'succeeded'
            except Exception as exc:
                record.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
            # Never label a fixture's newly assigned execution timestamp as collection.
            for e in state.transport_evidence:
                original = fixtures.get((e.get('from'), e.get('to'), e.get('requested_mode', e.get('mode'))))
                if original:
                    e['queried_at'] = original.get('queried_at')
                    e['expires_at'] = original.get('expires_at')
                else:
                    e['queried_at'] = e['expires_at'] = None
                e['historical_replay'] = True
            record.update(state=state.model_dump(mode='json'), elapsed_seconds=time.perf_counter()-started, finished_at=now())
            write(dest, record)
            write(out / 'evidence' / path.name, evaluate(record, cases[raw['case_id']], catalog))
            print(version, path.stem, record['status'], str(record.get('error', ''))[:180], flush=True)
            completed += 1
            if limit and completed >= limit:
                break
    records = [read(p) for p in (out / 'trials').glob('*.json')]
    write(out / 'execution_summary.json', {'at': now(), 'version': version, 'trials': len(records),
        'succeeded': sum(r['status'] == 'succeeded' for r in records), 'network_calls': 0,
        'subjective_review': 'pending', 'claim': 'Development replay only; not online quality acceptance.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['freeze', 'run'])
    parser.add_argument('version')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.command == 'freeze':
        freeze(args.version)
    else:
        asyncio.run(run(args.version, args.limit))
