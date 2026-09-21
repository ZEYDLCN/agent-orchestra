from pathlib import Path

import fakeredis
import pytest

from orchestrator.config import settings
from orchestrator.llm.base import LLMProvider
from orchestrator.llm.mock_provider import MockProvider
from orchestrator.memory import SharedMemory
from orchestrator.messaging import MessageBus
from orchestrator.models import Task
from worker.executor import run_task

SEED_DIR = Path(__file__).resolve().parent.parent / "target_repo_seed"


class AlwaysFailsLLM(LLMProvider):
    name = "broken"

    def generate(self, prompt: str) -> str:
        raise RuntimeError("llm is down")


@pytest.fixture(autouse=True)
def disable_sandbox(monkeypatch):
    # unit tests shouldn't need Docker / a built sandbox image; sandboxing
    # itself is covered by a separate live/manual check (see README)
    monkeypatch.setattr(settings, "sandbox_enabled", False)
    monkeypatch.setattr(settings, "environment", "development")


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
    assert result["execution_status"] == "completed"
    assert result["analysis_status"] == "completed"
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


def test_llm_failure_does_not_fail_the_whole_task(deps):
    """A successful backtest with a broken LLM should still be a
    successful task -- only the analysis step failed, not the execution."""
    _, memory, bus = deps
    task = Task(job_id="job-1", payload={"params": {"fast_ma": 5, "slow_ma": 200}})

    result = run_task(task, SEED_DIR, "worker-1", AlwaysFailsLLM(), memory, bus)

    assert result["execution_status"] == "completed"
    assert result["analysis_status"] == "failed"
    assert result["commentary"] is None
    assert isinstance(result["score"], float)
