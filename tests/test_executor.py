from pathlib import Path

import fakeredis
import pytest

from orchestrator.llm.mock_provider import MockProvider
from orchestrator.memory import SharedMemory
from orchestrator.messaging import MessageBus
from orchestrator.models import Task
from worker.executor import run_task

SEED_DIR = Path(__file__).resolve().parent.parent / "target_repo_seed"


@pytest.fixture
def deps():
    server = fakeredis.FakeServer()
    memory = SharedMemory.__new__(SharedMemory)
    memory.client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    bus = MessageBus.__new__(MessageBus)
    bus.client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    return MockProvider(), memory, bus


def test_run_task_returns_score_and_commentary(deps):
    llm, memory, bus = deps
    task = Task(job_id="job-1", payload={"params": {"fast_ma": 5, "slow_ma": 200}})

    result = run_task(task, SEED_DIR, "worker-1", llm, memory, bus)

    assert result["worker_id"] == "worker-1"
    assert result["params"] == {"fast_ma": 5, "slow_ma": 200}
    assert isinstance(result["score"], float)
    assert result["commentary"]
    assert result["llm_provider"] == "mock"


def test_run_task_records_to_shared_memory(deps):
    llm, memory, bus = deps
    task = Task(job_id="job-1", payload={"params": {"fast_ma": 10, "slow_ma": 100}})

    run_task(task, SEED_DIR, "worker-1", llm, memory, bus)

    entries = memory.all_entries()
    assert len(entries) == 1
    assert entries[0]["params"] == {"fast_ma": 10, "slow_ma": 100}


def test_run_task_uses_similar_past_entries_in_prompt(deps):
    llm, memory, bus = deps
    memory.record({"params": {"fast_ma": 5, "slow_ma": 200}, "score": 96.0, "worker_id": "w0"})

    task = Task(job_id="job-1", payload={"params": {"fast_ma": 5, "slow_ma": 199}})
    result = run_task(task, SEED_DIR, "worker-1", llm, memory, bus)

    # mock provider echoes the prompt back, so the past entry should show up
    assert "96.0" in result["commentary"] or "200" in result["commentary"]
