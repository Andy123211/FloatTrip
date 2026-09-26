import hashlib

import pytest

from .common import read, write
from .reliability_pilot import archive_budget_interruption, freeze_reviews, network_limit


def test_quota_extension_requires_explicit_ordered_authorization(tmp_path):
    write(tmp_path/'experiment.json', {'network_limit':300})
    assert network_limit(tmp_path) == 300
    extension = {'previous_limit':300, 'additional_requests':200,
                 'explicit_user_authorization':'授权新增最多 200 次'}
    write(tmp_path/'quota_extensions/001.json', extension)
    assert network_limit(tmp_path) == 500
    assert read(tmp_path/'experiment.json')['network_limit'] == 300
    write(tmp_path/'quota_extensions/002.json', extension)
    with pytest.raises(SystemExit, match='Invalid quota extension'):
        network_limit(tmp_path)


def test_resume_preserves_interrupted_bytes_and_dialogue_database(tmp_path):
    key = 'case.dialogue.1'
    trial = tmp_path/'trials'/f'{key}.json'
    write(trial, {'status':'budget_interrupted', 'amap_request_cache':[{'cache_hit':True}]})
    original = trial.read_bytes()
    db = trial.with_suffix('.sqlite')
    db.write_bytes(b'isolated partial conversation')
    write(tmp_path/'logs'/f'{key}.json', {'stderr':'AMAP_NETWORK_BUDGET_EXHAUSTED'})
    dest = tmp_path/archive_budget_interruption(tmp_path, key)
    assert dest.read_bytes() == original
    assert (dest.parent/db.name).read_bytes() == b'isolated partial conversation'
    assert not trial.exists() and not db.exists()
    preserved = read(dest.parent/'preservation.json')
    assert preserved['files']['trial.json'] == hashlib.sha256(original).hexdigest()


def test_algorithm_failures_cannot_be_retried_as_budget_interruptions(tmp_path):
    write(tmp_path/'trials/case.planning.1.json', {'status':'failed'})
    with pytest.raises(ValueError, match='Only a budget interruption'):
        archive_budget_interruption(tmp_path, 'case.planning.1')
    assert read(tmp_path/'trials/case.planning.1.json')['status'] == 'failed'


def test_partial_review_seal_can_extend_but_prior_scores_cannot_change(tmp_path):
    review = {'reviewer':'test', 'reviewed_at':'2026-09-21',
              'blind_to_variant':True, 'blind_to_internal_objective':True,
              'scores':dict.fromkeys(['popularity','personalization','route','validity'],4),
              'reasons':{'test':'synthetic review'}, 'confidence':'high',
              'evidence_references':['synthetic']}
    for index in (1,2):
        name = f'review-{index:03}.json'
        write(tmp_path/'blind_packets'/name, {'status':'succeeded','evidence':{'hard_violations':[]}})
        write(tmp_path/'blind_reviews'/name, review)
        freeze_reviews(tmp_path)
    assert read(tmp_path/'reviews_freeze.json')['count'] == 2
    assert len(list((tmp_path/'review_seal_history').glob('*.json'))) == 1
    changed = {**review, 'scores':{**review['scores'],'route':5}}
    write(tmp_path/'blind_reviews/review-001.json', changed)
    with pytest.raises(SystemExit, match='Review seal changed'):
        freeze_reviews(tmp_path)
