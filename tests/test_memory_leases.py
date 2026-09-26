"""Isolated multi-worker ownership and fenced write regression tests."""
import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.chat.memory_models import MemoryExtractionResult
from app.chat.memory_service import MemoryExtractionWorker
from app.core.database import get_conn, init_db
from app.core.travel_memory import MemoryJobRepository, MemoryLeaseLost, MemoryRepository
from app.runtime.repositories import ConversationRepository


@pytest.fixture
def memory(tmp_path):
    path = tmp_path / 'memory.db'
    init_db(path)
    with get_conn(path) as conn:
        conn.execute("INSERT INTO users VALUES('u','u','unused','now')")
    conversations = ConversationRepository(path)
    conversation = conversations.create('u')
    message = conversations.add_message('u', conversation['id'], 'user', '我一般喜欢博物馆')
    jobs = MemoryJobRepository(path)
    conversations.archive('u', conversation['id'])
    return path, jobs, message


def expire(path, job):
    with get_conn(path) as conn:
        conn.execute("UPDATE memory_extraction_jobs SET lease_expires_at='1970-01-01' WHERE id=?", (job['id'],))


def result(message, **changes):
    return MemoryExtractionResult.model_validate({'items':[{
        'action':'add', 'category':'attraction_preference', 'value_text':'喜欢博物馆',
        'polarity':'prefer', 'explicitness':'explicit',
        'evidence_sequences':[message['sequence']], **changes,
    }]})


def test_only_one_process_can_claim_and_live_work_survives_initialization(memory):
    path, jobs, _ = memory
    code = ('import json,sys; from app.core.travel_memory import MemoryJobRepository; '
            'print(json.dumps(MemoryJobRepository(sys.argv[1]).claim_next(sys.argv[2])))')
    def claim(owner):
        return json.loads(subprocess.check_output([sys.executable, '-c', code, str(path), owner], text=True))
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(claim, ['instance-a','instance-b']))
    assert sum(item is not None for item in claimed) == 1
    original = next(item for item in claimed if item)
    init_db(path)
    assert jobs.claim_next('instance-c') is None
    assert jobs.renew(original)


def test_expired_attempt_cannot_write_renew_finish_or_fail_replacement(memory):
    path, jobs, message = memory
    old = jobs.claim_next('a')
    expire(path, old)
    new = jobs.claim_next('b')
    assert new['id'] == old['id'] and new['lease_token'] != old['lease_token']
    assert not jobs.renew(old)
    worker = MemoryExtractionWorker(path)
    for operation in (
        lambda: worker._commit_chunk(old, [message], [], result(message)),
        lambda: jobs.complete(old), lambda: jobs.fail(old, 'LateError'), lambda: jobs.release(old),
    ):
        with pytest.raises(MemoryLeaseLost): operation()
    assert MemoryRepository(path).list('u') == []
    worker._commit_chunk(new, [message], [], result(message))
    jobs.complete(new)
    assert len(MemoryRepository(path).list('u')) == 1
    with get_conn(path) as conn:
        assert conn.execute('SELECT finalization_status FROM conversation_memory_states').fetchone()[0] == 'succeeded'


def test_committed_chunk_is_not_extracted_again_after_takeover(memory):
    path, jobs, message = memory
    old = jobs.claim_next('a')
    worker = MemoryExtractionWorker(path)
    worker._commit_chunk(old, [message], [], result(message))
    before = MemoryRepository(path).revision('u')
    expire(path, old)
    new = jobs.claim_next('b')
    assert new['applied_through_sequence'] == message['sequence']
    # No remaining messages: this must not even construct a model.
    with patch('app.chat.memory_service.build_structured_llm', side_effect=AssertionError('replayed chunk')):
        assert asyncio.run(worker.process(new))['active'] == 0
    jobs.complete(new)
    assert MemoryRepository(path).revision('u') == before


def test_fact_replacement_and_chunk_checkpoint_roll_back_together(memory):
    path, jobs, message = memory
    facts = MemoryRepository(path)
    old = facts.create('u', category='attraction_preference', value_text='喜欢乐园')
    active = facts.list('u')
    job = jobs.claim_next('a')
    worker = MemoryExtractionWorker(path)
    with patch.object(worker.facts, 'supersede', side_effect=RuntimeError('crash before checkpoint')):
        with pytest.raises(RuntimeError):
            worker._commit_chunk(job, [message], active, result(message, action='replace', supersedes_fact_ids=[old['id']]))
    assert [fact['id'] for fact in facts.list('u')] == [old['id']]
    with get_conn(path) as conn:
        assert conn.execute('SELECT applied_through_sequence FROM memory_extraction_jobs').fetchone()[0] == 0


def test_completion_requires_committed_progress_and_expiry_has_retry_limit(memory):
    path, jobs, _ = memory
    for attempt in range(5):
        job = jobs.claim_next('worker')
        assert job['attempts'] == attempt + 1
        with pytest.raises(ValueError, match='unapplied'): jobs.complete(job)
        expire(path, job)
    assert jobs.claim_next('worker') is None
    with get_conn(path) as conn:
        assert conn.execute('SELECT status FROM memory_extraction_jobs').fetchone()[0] == 'failed'
        assert conn.execute('SELECT finalization_status FROM conversation_memory_states').fetchone()[0] == 'failed'


def test_second_worker_start_does_not_reset_live_job_and_heartbeat_extends_it(memory):
    path, jobs, _ = memory
    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()
        class SlowModel:
            calls = 0
            async def ainvoke(self, messages):
                self.calls += 1
                entered.set()
                await finish.wait()
                return {'items':[]}
        model = SlowModel()
        first = MemoryExtractionWorker(path,llm=model,poll_seconds=.01,lease_seconds=.6)
        second = MemoryExtractionWorker(path,llm=model,poll_seconds=.01,lease_seconds=.6)
        await first.start()
        try:
            await asyncio.wait_for(entered.wait(), 2)
            with get_conn(path) as conn:
                before = dict(conn.execute('SELECT * FROM memory_extraction_jobs').fetchone())
            await second.start()
            # Wait for a real renewal rather than assuming a scheduling delay.
            async def renewed():
                while True:
                    with get_conn(path) as conn:
                        row = dict(conn.execute('SELECT * FROM memory_extraction_jobs').fetchone())
                    if (row['lease_expires_at'] > before['lease_expires_at']
                            and datetime.now(timezone.utc) > datetime.fromisoformat(before['lease_expires_at'])):
                        return row
                    await asyncio.sleep(.01)
            row = await asyncio.wait_for(renewed(), 2)
            assert row['lease_token'] == before['lease_token']
            assert row['attempts'] == 1 and model.calls == 1
            finish.set()
            async def succeeded():
                while True:
                    with get_conn(path) as conn:
                        status = conn.execute('SELECT status FROM memory_extraction_jobs').fetchone()[0]
                    if status == 'succeeded': return
                    await asyncio.sleep(.01)
            await asyncio.wait_for(succeeded(), 2)
        finally:
            await first.stop()
            await second.stop()
    asyncio.run(scenario())


def test_legacy_running_job_is_migrated_and_recoverable(memory):
    path, jobs, _ = memory
    with get_conn(path) as conn:
        conn.execute("UPDATE memory_extraction_jobs SET status='running',attempts=1")
        for column in ('lease_owner','lease_token','lease_expires_at','applied_through_sequence'):
            conn.execute(f'ALTER TABLE memory_extraction_jobs DROP COLUMN {column}')
    init_db(path)
    job = jobs.claim_next('new-version')
    assert job['attempts'] == 2 and job['applied_through_sequence'] == 0
    assert job['lease_token'] and job['lease_expires_at']


def test_expiry_during_chunk_rolls_back_facts_and_progress(memory):
    path, jobs, message = memory
    job = jobs.claim_next('worker')
    worker = MemoryExtractionWorker(path)
    real_apply = worker._apply
    def expires_before_commit(job, messages, active, parsed, conn):
        stats = real_apply(job, messages, active, parsed, conn)
        conn.execute("UPDATE memory_extraction_jobs SET lease_expires_at='1970-01-01' WHERE id=?", (job['id'],))
        return stats
    with patch.object(worker, '_apply', side_effect=expires_before_commit):
        with pytest.raises(MemoryLeaseLost):
            worker._commit_chunk(job, [message], [], result(message))
    assert MemoryRepository(path).list('u') == []
    with get_conn(path) as conn:
        assert conn.execute('SELECT applied_through_sequence FROM memory_extraction_jobs').fetchone()[0] == 0


def test_worker_shutdown_releases_its_own_lease(memory):
    path, jobs, _ = memory
    async def scenario():
        entered = asyncio.Event()
        class Model:
            async def ainvoke(self, messages):
                entered.set()
                await asyncio.Event().wait()
        worker = MemoryExtractionWorker(path,llm=Model(),poll_seconds=.01)
        await worker.start()
        await asyncio.wait_for(entered.wait(), 2)
        await worker.stop()
        claimed = jobs.claim_next('replacement')
        assert claimed and claimed['lease_owner'] == 'replacement'
    asyncio.run(scenario())
