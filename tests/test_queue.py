import fakeredis
import pytest

from orchestrator.models import Task
from orchestrator.queue import TaskQueue


@pytest.fixture
def queue():
    q = TaskQueue.__new__(TaskQueue)
    q.client = fakeredis.FakeStrictRedis(decode_responses=True)
    return q


def test_push_pop_roundtrip(queue):
    task = Task(job_id="job-1", payload={"params": {"fast_ma": 5, "slow_ma": 50}})
    queue.push_task(task)

    popped = queue.pop_task(timeout=1)
    assert popped is not None
    assert popped.task_id == task.task_id
    assert popped.payload == task.payload


def test_mark_done_updates_status_and_result(queue):
    task = Task(job_id="job-1", payload={"params": {}})
    queue.push_task(task)

    queue.mark_running(task.task_id, "worker-1")
    running = queue.get_task(task.task_id)
    assert running.status == "running"
    assert running.assigned_worker == "worker-1"

    queue.mark_done(task.task_id, {"score": 42.0})
    done = queue.get_task(task.task_id)
    assert done.status == "done"
    assert done.result == {"score": 42.0}


def test_get_job_tasks_returns_all_tasks_for_job(queue):
    for i in range(3):
        queue.push_task(Task(job_id="job-x", payload={"i": i}))
    queue.push_task(Task(job_id="job-y", payload={"i": 99}))

    tasks = queue.get_job_tasks("job-x")
    assert len(tasks) == 3
    assert all(t.job_id == "job-x" for t in tasks)


def test_pop_task_returns_none_on_empty_queue(queue):
    assert queue.pop_task(timeout=1) is None
