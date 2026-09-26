"""Offline delivery audit and common evidence metrics; never calls providers."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import math
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys

from .common import ROOT, digest, now, read, write
from .evidence import Catalog, evaluate
from .reliability_pilot import DEFAULT, network_limit, retained_attempts


def audit(out: Path):
    mismatches = []
    checked = 0

    def verify(base, hashes, canonical=False):
        nonlocal checked
        for name, expected in hashes.items():
            path = base / name
            actual = (digest(read(path)) if canonical else hashlib.sha256(path.read_bytes()).hexdigest()) if path.exists() else None
            checked += 1
            if actual != expected:
                mismatches.append(str(path))

    verify(out / 'baseline', read(out / 'baseline_manifest.json')['files'])
    exp = read(out / 'experiment.json')
    arms = exp.get('arms', ['C1', 'C2'])
    verify(out, exp['frozen_hashes'])
    verify(out, read(out / 'reviews_freeze.json')['hashes'], True)
    for seal in (out/'review_seal_history').glob('*.json'):
        verify(out, read(seal)['hashes'], True)
    policy = read(out / 'cache_policy_v1/policy.json')
    verify(out / 'cache_policy_v1', policy['code_hashes'])
    deps = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines()
    if deps != exp['dependencies']:
        mismatches.append('dependencies')
    cases = {c['id']: c for c in read(out / 'frozen/cases.json')['cases']}
    catalog = Catalog(read(out / 'frozen/catalog.json'))
    db = sqlite3.connect(f'file:{out / "budget.sqlite"}?mode=ro', uri=True)
    network = dict(db.execute('select trial, count(*) from requests group by trial'))
    db.close()
    comparison = read(out / 'comparison.json')
    rows = []
    replay_count = 0
    for arm in arms:
        manifest = read(out / 'runs' / arm / 'manifest.json')
        verify(out / 'runtimes' / arm, manifest['source_hashes'])
    for row in comparison['trials']:
        arm, key = row['variant'], row['key']
        base = out / 'runs' / arm
        raw = read(base / 'trials' / f'{key}.json')
        attempts = retained_attempts(base, raw)
        for relative in raw.get('prior_attempts', []):
            folder = (base/relative).parent
            verify(folder, read(folder/'preservation.json')['files'])
        evidence = read(base / 'evidence' / f'{key}.json')
        if evaluate(raw, cases[row['case_id']], catalog) != evidence:
            mismatches.append(f'replay:{arm}:{key}')
        replay_count += 1
        if raw.get('source_changes_before') or raw.get('source_changes_after'):
            mismatches.append(f'per_trial_source_drift:{arm}:{key}')
        if raw.get('cache_overlay_hash') != digest(policy):
            mismatches.append(f'per_trial_cache_policy_drift:{arm}:{key}')
        state = raw.get('state') or {}
        plan = state.get('final_plan') or {}
        pool = {c['poi_name']: c for c in state.get('candidate_pool', [])}
        edges = state.get('transport_evidence', [])
        counts = Counter()
        pending = Counter(x['kind'] for x in plan.get('pending_checks', []))
        selected = []
        for day in plan.get('days', []):
            spots = [s for s in day['timeline'] if s.get('type') == 'attraction']
            for spot in spots:
                selected.append(spot['name'])
                counts['stops'] += 1
                counts['stops_with_opening_text'] += bool(spot.get('open_time'))
                counts['stops_reference_opening_unverified'] += any(
                    f['name'] == spot['name'] and '开放时间未独立核实' in f['notes']
                    for f in evidence['facts'])
                calendar = pool.get(spot['name'], {}).get('opening_calendar', {})
                rule = calendar.get('days', {}).get(day['date'], {})
                if calendar:
                    counts['stops_structured_calendar'] += 1
                    counts['stops_structured_unknown'] += rule.get('status') == 'unknown'
                    counts['stops_structured_partial'] += rule.get('status') == 'partial'
            for left, right in zip(spots, spots[1:]):
                counts['adjacent_edges'] += 1
                # Both arms use exact directed names from the same final candidate pool.
                # C1 lacks entity IDs and timestamps; do not invent their presence.
                hits = [e for e in edges if e.get('from') == left['name'] and e.get('to') == right['name']
                        and e.get('status') == 'estimated' and e.get('duration_minutes') is not None]
                if hits:
                    counts['edges_with_recorded_estimate'] += 1
                    counts['edges_with_expiry_record'] += bool(hits[-1].get('expires_at'))
                else:
                    counts['edges_without_recorded_estimate'] += 1
        usages = [u for attempt in attempts for u in attempt.get('model_usage', [])]
        tokens = sum((u.get('llm_output') or {}).get('token_usage', {}).get('total_tokens', 0) for u in usages)
        counts.update(network_attempts=network.get(arm + ':' + key, 0),
                      cache_hits=sum(bool(x.get('cache_hit')) for attempt in attempts for x in attempt.get('amap_request_cache', [])),
                      prior_budget_interruptions=len(attempts)-1,
                      recorded_model_calls=len(usages), recorded_tokens=tokens)
        must = cases[row['case_id']]['expectations']['must_visit']
        rows.append({k: row[k] for k in ('variant', 'city', 'case_id', 'mode', 'trial', 'key', 'review_id', 'status', 'score', 'passed', 'elapsed_seconds')} | {
            'counts': dict(counts), 'pending_counts': dict(pending),
            'production_validation_status': (plan.get('validation_summary') or {}).get('status'),
            'error': raw.get('error'), 'must_expected': len(must),
            'must_satisfied': len(evidence['must_visit_satisfied']),
            'selected_names': sorted(set(selected)), 'selected_groups': evidence['selected_groups'],
            'coverage': evidence['coverage'], 'hard_violations': row['hard_violations'],
        })
    aggregates = []
    for city in ('上海', '南京'):
        for arm in arms:
            for mode in ('planning', 'dialogue'):
                subset = [r for r in rows if (r['city'], r['variant'], r['mode']) == (city, arm, mode)]
                if not subset:
                    continue
                counts, pending = Counter(), Counter()
                for r in subset:
                    counts.update(r['counts']); pending.update(r['pending_counts'])
                times = sorted(r['elapsed_seconds'] for r in subset if r['elapsed_seconds'] is not None)
                aggregates.append({'city': city, 'variant': arm, 'mode': mode, 'trials': len(subset),
                    'counts': dict(counts), 'pending_counts': dict(pending),
                    'validation_status': dict(Counter(r['production_validation_status'] or 'unavailable' for r in subset)),
                    'latency_median': statistics.median(times) if times else None, 'latency_p95_nearest_rank': times[math.ceil(.95 * len(times)) - 1] if times else None,
                    'must_expected': sum(r['must_expected'] for r in subset), 'must_satisfied': sum(r['must_satisfied'] for r in subset),
                    'coverage_mean': {stage: statistics.mean(r['coverage'][stage]['fraction_of_eligible'] for r in subset)
                                      for stage in subset[0]['coverage']}})
    stability = []
    for arm in arms:
        for case_id in cases:
            subset = [r for r in rows if r['variant'] == arm and r['case_id'] == case_id and r['mode'] == 'planning']
            if not subset:
                continue
            similarities = []
            for left, right in itertools.combinations(subset, 2):
                a, b = set(left['selected_groups']), set(right['selected_groups'])
                similarities.append(len(a & b) / len(a | b) if a | b else None)
            stability.append({'variant': arm, 'case_id': case_id, 'scores': [r['score'] for r in subset],
                'passed': sum(r['passed'] for r in subset), 'generated': sum(r['status'] == 'succeeded' for r in subset),
                'mean_score': statistics.mean(r['score'] for r in subset),
                'score_range': max(r['score'] for r in subset) - min(r['score'] for r in subset),
                'popular_group_pair_jaccard': similarities})
    result = {'at': now(), 'hash_checks': checked, 'replays': replay_count, 'mismatches': mismatches,
              'network_attempts': sum(network.values()), 'network_limit': network_limit(out, exp),
              'review_count': read(out / 'reviews_freeze.json')['count'],
              'postprocessor_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'comparison_postprocessor_sha256': hashlib.sha256(Path(__file__).with_name('reliability_pilot.py').read_bytes()).hexdigest(),
              'candidate_variant': arms[-1],
              'production_matches_frozen_candidate': all(hashlib.sha256((ROOT / n).read_bytes()).hexdigest() == v
                  for n, v in read(out / 'runs' / arms[-1] / 'manifest.json')['source_hashes'].items() if n.startswith('app/')),
              'limitations': ['Common transport metric means a recorded estimate, not a verified future timetable or expiry validation.',
                 'Reference opening completeness does not include supplemental manual review sources; raw opening text alone is not proof.',
                 'Model call/token totals cover captured callbacks only; no complete billing or cost claim.',
                 'Missing structured pending fields mean unavailable, not zero uncertainty. Partial calendars retain unresolved exceptions.',
                 'Postprocessing was extended after production freeze; frozen runtime and sealed review hashes are checked separately.'],
              'aggregates': aggregates, 'stability': stability, 'trials': rows}
    assert sum(network.values()) <= network_limit(out, exp)
    write(out / 'pilot_audit.json', result)
    print({'hash_checks': checked, 'replays': replay_count, 'mismatches': mismatches, 'network_attempts': sum(network.values())})
    if mismatches:
        raise SystemExit(1)


if __name__ == '__main__':
    audit(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT)
