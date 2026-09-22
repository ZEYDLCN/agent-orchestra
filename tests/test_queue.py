import time

import fakeredis
import pytest

from orchestrator.models import Task, TaskStatus
from orchestrator.queue import TaskQueue

# Real Redis's built-in cjson round-trips an empty Lua table back to `{}`
# (verified directly against redis:7-alpine); fakeredis's pure-Python/lupa
# Lua emulation instead produces `[]`, which fails Task's payload: dict
# validation. Tests below use a non-empty payload to sidestep that
# emulator-only quirk rather than paper over it in production code.
NONEMPTY_PAYLOAD = {"note": "test"}


@pytest.fixture
def queue():
    # Real __init__ (talks to a not-yet-connected real Redis client --
    # redis-py connects lazily, so this doesn't actually touch the
    # network), then rebind_client swaps in fakeredis *and* re-registers
    # every Lua script against it in one place, so this fixture can't
    # silently drift out of sync with queue.py's own script list the way
    # a hand-maintained one here would.
    q = TaskQueue()
    q.rebind_client(fakeredis.FakeStrictRedis(decode_responses=True))
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
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD, required_capability="trading")
    queue.push_task(task)

    assert queue.pop_task(["backtest"], "worker-1", timeout=1) is None
    popped = queue.pop_task(["trading"], "worker-1", timeout=1)
    assert popped is not None
    assert popped.task_id == task.task_id


def test_ack_marks_done_with_result(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    queue.pop_task(["backtest"], "worker-1", timeout=1)

    queue.ack(task.task_id, {"score": 42.0})

    done = queue.get_task(task.task_id)
    assert done.status == TaskStatus.DONE
    assert done.result == {"score": 42.0}
    assert done.lease_expires_at is None


def test_nack_retries_until_max_attempts_then_dead_letters(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD, max_attempts=2)
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

    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
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


def test_reaper_cannot_requeue_task_acked_after_expiry_observation(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    claimed = queue.pop_task(["backtest"], "worker-1", timeout=1)

    claimed.lease_expires_at = time.time() - 1
    queue.save_task(claimed)
    observed = queue.get_task(task.task_id)

    assert queue.ack(task.task_id, {"score": 42}, claimed.lease_token) is True
    assert queue.nack(
        task.task_id,
        "lease expired",
        lease_token=observed.lease_token,
        require_expired=True,
    ) is False
    assert queue.get_task(task.task_id).status == TaskStatus.DONE


def test_reaper_cannot_requeue_lease_renewed_after_expiry_observation(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    claimed = queue.pop_task(["backtest"], "worker-1", timeout=1)

    claimed.lease_expires_at = time.time() - 1
    queue.save_task(claimed)
    observed = queue.get_task(task.task_id)
    assert queue.renew_lease(task.task_id, claimed.lease_token) is True

    assert queue.nack(
        task.task_id,
        "lease expired",
        lease_token=observed.lease_token,
        require_expired=True,
    ) is False
    assert queue.get_task(task.task_id).status == TaskStatus.RUNNING


def test_reap_recovers_task_moved_to_processing_before_claim(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    queue.client.lmove("queue:pending:backtest", "queue:processing", "LEFT", "RIGHT")

    assert queue.reap_expired() == [task.task_id]
    recovered = queue.pop_task(["backtest"], "worker-2", timeout=1)
    assert recovered is not None
    assert recovered.task_id == task.task_id


def test_stale_lease_token_ack_is_rejected_after_reclaim(queue):
    """The race this whole fencing-token scheme exists for: worker A is
    slow, not dead -- its lease expires and the reaper hands the task to
    worker B before A finally calls ack(). A's ack must not be allowed to
    clobber B's (still in-progress) claim or a result B hasn't produced
    yet."""
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)

    worker_a_claim = queue.pop_task(["backtest"], "worker-A", timeout=1)
    stale_token = worker_a_claim.lease_token

    expired = queue.get_task(task.task_id)
    expired.lease_expires_at = time.time() - 1
    queue.save_task(expired)
    queue.reap_expired()

    worker_b_claim = queue.pop_task(["backtest"], "worker-B", timeout=1)
    assert worker_b_claim.lease_token != stale_token

    assert queue.ack(task.task_id, {"score": 1.0}, lease_token=stale_token) is False
    still_running = queue.get_task(task.task_id)
    assert still_running.status == TaskStatus.RUNNING
    assert still_running.assigned_worker == "worker-B"

    assert queue.ack(task.task_id, {"score": 2.0}, lease_token=worker_b_claim.lease_token) is True
    final = queue.get_task(task.task_id)
    assert final.status == TaskStatus.DONE
    assert final.result == {"score": 2.0}


def test_renew_lease_extends_expiry_for_matching_token(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    claimed = queue.pop_task(["backtest"], "worker-1", timeout=1)
    original_expiry = claimed.lease_expires_at

    time.sleep(0.05)
    assert queue.renew_lease(task.task_id, claimed.lease_token) is True

    renewed = queue.get_task(task.task_id)
    assert renewed.lease_expires_at > original_expiry


def test_renew_lease_rejects_stale_token(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    claimed = queue.pop_task(["backtest"], "worker-1", timeout=1)

    assert queue.renew_lease(task.task_id, "not-the-real-token") is False
    unchanged = queue.get_task(task.task_id)
    assert unchanged.lease_expires_at == claimed.lease_expires_at


def test_reap_expired_ignores_tasks_with_time_left(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    queue.pop_task(["backtest"], "worker-1", timeout=1)

    assert queue.reap_expired() == []


def test_release_lease_requeues_without_counting_as_attempt(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    claimed = queue.pop_task(["backtest"], "worker-1", timeout=1)
    assert claimed.attempt_count == 1

    queue.release_lease(task.task_id, claimed.lease_token)

    after = queue.get_task(task.task_id)
    assert after.status == TaskStatus.PENDING
    assert after.attempt_count == 1  # not incremented again on next pop until claimed


def test_release_lease_rejects_stale_worker_token(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    first_claim = queue.pop_task(["backtest"], "worker-1", timeout=1)
    first_claim.lease_expires_at = time.time() - 1
    queue.save_task(first_claim)
    queue.reap_expired()
    second_claim = queue.pop_task(["backtest"], "worker-2", timeout=1)

    assert queue.release_lease(task.task_id, first_claim.lease_token) is False
    current = queue.get_task(task.task_id)
    assert current.status == TaskStatus.RUNNING
    assert current.lease_token == second_claim.lease_token


def test_request_cancel_pending_task_removes_it_from_queue(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)

    assert queue.request_cancel(task.task_id) is True

    after = queue.get_task(task.task_id)
    assert after.status == TaskStatus.CANCELLED
    assert queue.pop_task(["backtest"], "worker-1", timeout=1) is None


def test_request_cancel_running_task_dead_letters_on_nack(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    queue.pop_task(["backtest"], "worker-1", timeout=1)

    queue.request_cancel(task.task_id)
    queue.nack(task.task_id, "cancelled mid-flight")

    after = queue.get_task(task.task_id)
    assert after.status == TaskStatus.CANCELLED


def test_retry_task_resets_failed_task(queue):
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD, max_attempts=1)
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
    task = Task(job_id="job-1", payload=NONEMPTY_PAYLOAD)
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
    t1 = Task(job_id="job-s", payload=NONEMPTY_PAYLOAD)
    t2 = Task(job_id="job-s", payload=NONEMPTY_PAYLOAD, max_attempts=1)
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
    task = Task(job_id="job-del", payload=NONEMPTY_PAYLOAD)
    queue.push_task(task)
    queue.register_job("job-del")

    removed = queue.delete_job("job-del")

    assert removed == 1
    assert queue.get_task(task.task_id) is None
    assert queue.job_exists("job-del") is False
