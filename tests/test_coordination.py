import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from module_families.coordination import (
    CoordinationClient,
    CoordinationError,
    CoordinationQueue,
    coordination_server,
)


@pytest.fixture
def queue(tmp_path):
    return CoordinationQueue(tmp_path / 'queue.sqlite')


def finish(queue, lease):
    return queue.submit(lease['id'], lease['worker'], lease['fence'], {'candidate':'sha256:abc'})


def test_immutable_idempotent_payload(queue):
    task = queue.enqueue({'work':1}, task_id='task')
    assert queue.enqueue({'work':1}, task_id='task') == task
    with pytest.raises(CoordinationError, match='immutable'):
        queue.enqueue({'work':2}, task_id='task')
    assert len(task['payload_sha256']) == 64


def test_concurrent_claims(queue):
    for i in range(40):
        queue.enqueue({'i':i})
    with ThreadPoolExecutor(max_workers=20) as pool:
        claims = list(pool.map(lambda i: queue.claim(f'worker-{i}'), range(60)))
    ids = [claim['id'] for claim in claims if claim]
    assert len(ids) == len(set(ids)) == 40


def test_reclaim_fences_and_reopens(tmp_path):
    queue = CoordinationQueue(tmp_path / 'queue.sqlite')
    queue.enqueue({'task':1})
    stale = queue.claim('worker', lease_seconds=.01)
    time.sleep(.02)
    queue = CoordinationQueue(tmp_path / 'queue.sqlite')
    current = queue.claim('worker')
    assert current['fence'] == stale['fence']+1
    with pytest.raises(CoordinationError, match='stale'):
        finish(queue, stale)
    with pytest.raises(CoordinationError, match='stale'):
        queue.renew(stale['id'], 'worker', stale['fence'])
    assert finish(queue, current)['state'] == 'submitted'


def test_submission_idempotence_and_admin_accept(queue):
    queue.enqueue({'task':1})
    lease = queue.claim('a')
    submitted = finish(queue, lease)
    assert finish(queue, lease) == submitted
    with pytest.raises(CoordinationError):
        queue.accept(lease['id'])
    accepted = queue.accept(lease['id'], submitted['submission_sha256'], {'evidence':'evaluated'})
    assert accepted['state'] == 'accepted'
    assert finish(queue, lease) == accepted
    with pytest.raises(CoordinationError):
        queue.submit(lease['id'], 'a', lease['fence'], {'candidate':'other'})


def test_dependency_waits_for_acceptance(queue):
    first = queue.enqueue({'first':1}, task_id='first')
    queue.enqueue({'second':1}, task_id='second', prerequisites=['first'], priority=100)
    lease = queue.claim('a')
    assert lease['id'] == first['id']
    submitted = finish(queue, lease)
    assert queue.claim('b') is None
    queue.accept('first', submitted['submission_sha256'])
    assert queue.claim('b')['id'] == 'second'


def test_dependency_cycle_and_unknown_rejected(queue):
    with pytest.raises(CoordinationError):
        queue.enqueue({}, task_id='a', prerequisites=['b'])
    with pytest.raises(CoordinationError):
        queue.enqueue({}, task_id='a', prerequisites=['a'])
    queue.enqueue({}, task_id='a')
    queue.enqueue({}, task_id='b', prerequisites=['a'])
    with pytest.raises(CoordinationError):
        queue.enqueue({}, task_id='a', prerequisites=['b'])


def test_retries_exhaust_budget(queue):
    queue.enqueue({}, task_id='a', max_attempts=2)
    first = finish(queue, queue.claim('a'))
    assert queue.reject('a', 'failed eval', submission_sha256=first['submission_sha256'])['state'] == 'pending'
    second = finish(queue, queue.claim('b'))
    assert second['attempts'] == 2
    with pytest.raises(CoordinationError):
        queue.accept('a', 'wrong hash')
    assert queue.reject('a', 'failed eval', submission_sha256=second['submission_sha256'])['state'] == 'failed'
    assert queue.claim('c') is None


def test_expired_final_attempt(queue):
    queue.enqueue({}, max_attempts=1)
    lease = queue.claim('a', lease_seconds=.01)
    time.sleep(.02)
    assert queue.claim('b') is None
    assert queue.get(lease['id'])['state'] == 'failed'


def test_renew_owner_and_expiry(queue):
    queue.enqueue({})
    lease = queue.claim('a')
    with pytest.raises(CoordinationError):
        queue.renew(lease['id'], 'b', lease['fence'])
    renewed = queue.renew(lease['id'], 'a', lease['fence'], lease_seconds=100)
    assert renewed['lease_until'] > lease['lease_until']


def test_priority_and_pagination(queue):
    queue.enqueue({'low':1}, priority=-1)
    queue.enqueue({'high':1}, priority=100)
    assert queue.claim('a')['payload'] == {'high':1}
    assert len(queue.list_tasks(limit=1, offset=1)) == 1
    assert len(queue.list_tasks(state='pending')) == 1
    with pytest.raises(CoordinationError):
        queue.list_tasks(limit=1001)


def test_http_auth_and_worker_scope(queue):
    admin_token = 'admin-secret-0123456789'
    worker_token = 'worker-secret-0123456789'
    server = coordination_server(queue, {admin_token:{'role':'admin'}, worker_token:{'role':'worker','worker':'alice'}})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    admin = CoordinationClient(url, admin_token)
    worker = CoordinationClient(url, worker_token)
    try:
        with pytest.raises(CoordinationError, match='401'):
            CoordinationClient(url, 'wrong').claim()
        with pytest.raises(CoordinationError, match='403'):
            worker.enqueue(payload={})
        admin.enqueue(payload={'work':1}, task_id='a')
        with pytest.raises(CoordinationError, match='403'):
            worker.claim(worker='bob')
        lease = worker.claim()
        assert lease['worker'] == 'alice'
        worker.renew(task_id='a', fence=lease['fence'])
        task = worker.submit(task_id='a', fence=lease['fence'], submission={'candidate':'ref','passed':True})
        assert task['state'] == 'submitted'
        with pytest.raises(CoordinationError, match='403'):
            worker.accept(task_id='a', submission_sha256=task['submission_sha256'])
        assert admin.accept(task_id='a', submission_sha256=task['submission_sha256'])['state'] == 'accepted'
        admin.enqueue(payload={},task_id='b')
        with pytest.raises(CoordinationError, match='403'):
            worker.get(task_id='b')
        request = Request(url+'/enqueue', data=b'{}', headers={'Authorization':'Bearer '+admin_token, 'Content-Length':'1100001'}, method='POST')
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        assert error.value.code == 413
        assert json.loads(error.value.read())['error']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_credentials_required(queue):
    with pytest.raises(CoordinationError):
        coordination_server(queue, {})
    with pytest.raises(CoordinationError):
        coordination_server(queue, {'short':{'role':'admin'}})


def _process_claim(path, output):
    queue = CoordinationQueue(path)
    output.put(queue.claim('process'))


def test_process_claim_atomicity(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context('spawn')
    path = str(tmp_path / 'process.sqlite')
    queue = CoordinationQueue(path)
    queue.enqueue({'one':1})
    output = context.Queue()
    processes = [context.Process(target=_process_claim, args=(path, output)) for _ in range(4)]
    for process in processes:
        process.start()
    results = [output.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    output.close()
    assert sum(result is not None for result in results) == 1


def test_results_page_has_byte_bound(queue):
    for index in range(10):
        queue.enqueue({'large':'x'*900_000}, task_id=str(index))
    page = queue.list_tasks(limit=100)
    assert 1 <= len(page) < 10
    rest = queue.list_tasks(offset=len(page))
    assert len(page) + len(rest) == 10


def test_readiness_counts_distinct_dependencies_and_duplicate_acceptance(queue):
    queue.enqueue({}, task_id='a')
    queue.enqueue({}, task_id='b')
    queue.enqueue({}, task_id='join', prerequisites=['a', 'a', 'b'], priority=100)
    assert queue.get('join')['unresolved_prerequisites'] == 2
    first = finish(queue, queue.claim('one'))
    accepted = queue.accept(first['id'], first['submission_sha256'])
    assert queue.accept(first['id'], first['submission_sha256']) == accepted
    assert queue.get('join')['unresolved_prerequisites'] == 1
    second = finish(queue, queue.claim('two'))
    queue.accept(second['id'], second['submission_sha256'])
    assert queue.get('join')['unresolved_prerequisites'] == 0
    assert queue.claim('three')['id'] == 'join'


def test_enqueue_after_acceptance_is_immediately_ready(queue):
    queue.enqueue({}, task_id='root')
    submission = finish(queue, queue.claim('worker'))
    queue.accept('root', submission['submission_sha256'])
    task = queue.enqueue({}, task_id='child', prerequisites=['root'])
    assert task['unresolved_prerequisites'] == 0
    assert queue.claim('child-worker')['id'] == 'child'


def test_rejection_and_submission_do_not_release_dependencies(queue):
    queue.enqueue({}, task_id='root')
    queue.enqueue({}, task_id='child', prerequisites=['root'], priority=100)
    submission = finish(queue, queue.claim('one'))
    queue.reject('root', 'retry', submission_sha256=submission['submission_sha256'])
    assert queue.get('child')['unresolved_prerequisites'] == 1
    submission = finish(queue, queue.claim('two'))
    assert queue.claim('three') is None
    queue.reject('root', 'failed', retry=False, submission_sha256=submission['submission_sha256'])
    assert queue.get('child')['unresolved_prerequisites'] == 1
    assert queue.claim('four') is None


def test_acceptance_releases_fanout_and_reopen_preserves_readiness(tmp_path):
    path = tmp_path / 'ready.sqlite'
    queue = CoordinationQueue(path)
    queue.enqueue({}, task_id='root')
    queue.enqueue_many([{'payload': {}, 'task_id': f'child-{i}', 'prerequisites': ['root'], 'priority': i} for i in range(100)])
    submission = finish(queue, queue.claim('root-worker'))
    queue.accept('root', submission['submission_sha256'])
    queue = CoordinationQueue(path)
    assert queue.claim('child-worker')['id'] == 'child-99'
    assert all(task['unresolved_prerequisites'] == 0 for task in queue.list_tasks())


def test_claim_query_uses_materialized_ready_index(queue):
    with queue._db() as db:
        query = "SELECT id FROM tasks INDEXED BY task_ready_priority WHERE state='pending' AND unresolved_prerequisites=0 ORDER BY priority DESC,created,id LIMIT 1"
        plan = ' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + query))
    assert 'task_ready_priority' in plan
    assert 'TEMP B-TREE' not in plan


def test_old_queue_schema_is_explicitly_rejected(tmp_path):
    import sqlite3
    path = tmp_path / 'old.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY)')
    with pytest.raises(CoordinationError, match='incompatible coordination schema'):
        CoordinationQueue(path)


def test_concurrent_accept_and_enqueue_cannot_lose_readiness(queue):
    queue.enqueue({}, task_id='root')
    submission = finish(queue, queue.claim('root-worker'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        acceptance = pool.submit(queue.accept, 'root', submission['submission_sha256'])
        insertion = pool.submit(queue.enqueue, {}, task_id='child', prerequisites=['root'])
        acceptance.result()
        insertion.result()
    assert queue.get('child')['unresolved_prerequisites'] == 0
    assert queue.claim('child-worker')['id'] == 'child'
