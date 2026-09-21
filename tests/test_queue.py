import time

import fakeredis
import pytest

from orchestrator.metrics import RedisMetrics
from orchestrator.models import Task, TaskStatus
from orchestrator.queue import TaskQueue


@pytest.fixture
def queue():
    q = TaskQueue.__new__(TaskQueue)
    q.client = fakeredis.FakeStrictRedis(decode_responses=True)
    q.metrics = RedisMetrics.__new__(RedisMetrics)
    q.metrics.client = q.client
    return q


def test_push_pop_roundtrip(queue):
    task = Task(job_id="job-1", payload={"params": {"fast_ma": 5, "slow_ma": 50}})
    queue.push_task(task)

    popped = queue.pop_task(["backtest"], "worker-1", timeout=1)
    assert popped is not None
    assert popped.task_id == task.task_id
    assert popped.payload == task.payload
    assert popped.status == TaskStatus.RUNNING
    assert popped.assigned_worker == "worker-1"
    assert popped.attempt_count == 1
    assert popped.lease_expires_at is not None


def test_pop_task_returns_none_on_empty_queue(queue):
    assert queue.pop_task(["backtest"], "worker-1", timeout=1) is None


def test_pop_task_respects_capability(queue):
    task = Task(job_id="job-1", payload={}, required_capability="trading")
    queue.push_task(task)

    assert queue.pop_task(["backtest"], "worker-1", timeout=1) is None
    popped = queue.pop_task(["trading"], "worker-1", timeout=1)
    assert popped is not None
    assert popped.task_id == task.task_id


def test_ack_marks_done_with_result(queue):
    task = Task(job_id="job-1", payload={})
    queue.push_task(task)
    queue.pop_task(["backtest"], "worker-1", timeout=1)

    queue.ack(task.task_id, {"score": 42.0})

    done = queue.get_task(task.task_id)
    assert done.status == TaskStatus.DONE
    assert done.result == {"score": 42.0}
    assert done.lease_expires_at is None


def test_nack_retries_until_max_attempts_then_dead_letters(queue):
    task = Task(job_id="job-1", payload={}, max_attempts=2)
    queue.push_task(task)

    queue.pop_task(["backtest"], "worker-1", timeout=1)
    queue.nack(task.task_id, "boom")
    after_first_failure = queue.get_task(task.task_id)
    assert after_first_failure.status == TaskStatus.PENDING  # attempt 1 of 2, retried

    queue.pop_task(["backtest"], "worker-1", timeout=1)
    queue.nack(task.task_id, "boom again")
    after_second_failure = queue.get_task(task.task_id)
    assert after_second_failure.status == TaskStatus.FAILED  # attempt 2 of 2, dead-lettered
    assert after_second_failure.error == "boom again"


def test_reap_expired_requeues_abandoned_task(queue):

    task = Task(job_id="job-1", payload={})
    queue.push_task(task)
    claimed = queue.pop_task(["backtest"], "worker-1", timeout=1)

    # simulate the worker crashing: force the lease into the past
    claimed.lease_expires_at = time.time() - 1
    queue.save_task(claimed)

    reclaimed = queue.reap_expired()

    assert reclaimed == [task.task_id]
    after = queue.get_task(task.task_id)
    assert after.status == TaskStatus.PENDING
    # it should be poppable again by another worker
    repopped = queue.pop_task(["backtest"], "worker-2", timeout=1)
    assert repopped is not None
    assert repopped.attempt_count == 2


def test_reap_expired_ignores_tasks_with_time_left(queue):
    task = Task(job_id="job-1", payload={})
    queue.push_task(task)
    queue.pop_task(["backtest"], "worker-1", timeout=1)

    assert queue.reap_expired() == []


def test_release_lease_requeues_without_counting_as_attempt(queue):
    task = Task(job_id="job-1", payload={})
    queue.push_task(task)
    claimed = queue.pop_task(["backtest"], "worker-1", timeout=1)
    assert claimed.attempt_count == 1

    queue.release_lease(task.task_id)

    after = queue.get_task(task.task_id)
    assert after.status == TaskStatus.PENDING
    assert after.attempt_count == 1  # not incremented again on next pop until claimed


def test_request_cancel_pending_task_removes_it_from_queue(queue):
    task = Task(job_id="job-1", payload={})
    queue.push_task(task)

    assert queue.request_cancel(task.task_id) is True

    after = queue.get_task(task.task_id)
    assert after.status == TaskStatus.CANCELLED
    assert queue.pop_task(["backtest"], "worker-1", timeout=1) is None


def test_request_cancel_running_task_dead_letters_on_nack(queue):
    task = Task(job_id="job-1", payload={})
    queue.push_task(task)
    queue.pop_task(["backtest"], "worker-1", timeout=1)

    queue.request_cancel(task.task_id)
    queue.nack(task.task_id, "cancelled mid-flight")

    after = queue.get_task(task.task_id)
    assert after.status == TaskStatus.CANCELLED


def test_retry_task_resets_failed_task(queue):
    task = Task(job_id="job-1", payload={}, max_attempts=1)
    queue.push_task(task)
    queue.pop_task(["backtest"], "worker-1", timeout=1)
    queue.nack(task.task_id, "boom")
    assert queue.get_task(task.task_id).status == TaskStatus.FAILED

    assert queue.retry_task(task.task_id) is True

    after = queue.get_task(task.task_id)
    assert after.status == TaskStatus.PENDING
    assert after.attempt_count == 0
    popped = queue.pop_task(["backtest"], "worker-1", timeout=1)
    assert popped.task_id == task.task_id


def test_retry_task_rejects_active_task(queue):
    task = Task(job_id="job-1", payload={})
    queue.push_task(task)
    assert queue.retry_task(task.task_id) is False  # still pending, nothing to retry


def test_get_job_tasks_returns_all_tasks_for_job(queue):
    for i in range(3):
        queue.push_task(Task(job_id="job-x", payload={"i": i}))
    queue.push_task(Task(job_id="job-y", payload={"i": 99}))

    tasks = queue.get_job_tasks("job-x")
    assert len(tasks) == 3
    assert all(t.job_id == "job-x" for t in tasks)


def test_get_job_summary_counts_by_status(queue):
    t1 = Task(job_id="job-s", payload={})
    t2 = Task(job_id="job-s", payload={}, max_attempts=1)
    queue.push_task(t1)
    queue.push_task(t2)

    queue.pop_task(["backtest"], "worker-1", timeout=1)
    queue.ack(t1.task_id, {"score": 1})
    queue.pop_task(["backtest"], "worker-1", timeout=1)
    queue.nack(t2.task_id, "boom")

    summary = queue.get_job_summary("job-s", use_cache=False)
    assert summary == {"total": 2, "done": 1, "failed": 1, "cancelled": 0, "pending_or_running": 0}


def test_list_recent_jobs_returns_newest_first(queue):
    queue.register_job("job-old")
    queue.register_job("job-new")

    jobs = queue.list_recent_jobs(limit=10)

    assert [job_id for job_id, _ in jobs] == ["job-new", "job-old"]


def test_list_recent_jobs_respects_limit(queue):
    for i in range(5):
        queue.register_job(f"job-{i}")

    jobs = queue.list_recent_jobs(limit=2)

    assert len(jobs) == 2


def test_delete_job_removes_tasks_and_index(queue):
    task = Task(job_id="job-del", payload={})
    queue.push_task(task)
    queue.register_job("job-del")

    removed = queue.delete_job("job-del")

    assert removed == 1
    assert queue.get_task(task.task_id) is None
    assert queue.job_exists("job-del") is False
