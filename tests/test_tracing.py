import fakeredis

from orchestrator.llm.base import LLMProvider
from orchestrator.models import LLMTrace
from orchestrator.tracing import TraceStore, traced_generate


class LegacyProvider(LLMProvider):
    name = "legacy:model"

    def generate(self, prompt: str) -> str:
        return "short response"


def make_store() -> TraceStore:
    store = TraceStore.__new__(TraceStore)
    store.client = fakeredis.FakeStrictRedis(decode_responses=True)
    return store


def test_trace_store_filters_and_aggregates_agent_usage():
    store = make_store()
    store.record(
        LLMTrace(
            run_id="run-1",
            job_id="job-1",
            role="planner",
            agent_id="planner",
            provider="ollama",
            model="qwen",
            input_tokens=40,
            output_tokens=10,
            total_tokens=50,
            duration_ms=20,
            cost_usd=0,
        )
    )
    store.record(
        LLMTrace(
            run_id="run-1",
            job_id="job-1",
            role="trader",
            agent_id="worker-1",
            provider="ollama",
            model="qwen",
            input_tokens=30,
            output_tokens=20,
            total_tokens=50,
            duration_ms=80,
            cost_usd=0,
        )
    )
    store.record(
        LLMTrace(
            run_id="run-2",
            role="reviewer",
            agent_id="reviewer",
            provider="mock",
            total_tokens=999,
        )
    )

    traces = store.list_recent(run_id="run-1")
    summary = store.summary(run_id="run-1")

    assert len(traces) == 2
    assert summary["calls"] == 2
    assert summary["total_tokens"] == 100
    assert {agent["agent_id"] for agent in summary["agents"]} == {"planner", "worker-1"}
    assert summary["cost_usd"] == 0


def test_generate_only_provider_is_traced_with_estimated_tokens():
    store = make_store()

    response = traced_generate(
        LegacyProvider(),
        "a sufficiently long prompt",
        store,
        role="planner",
        agent_id="planner",
        run_id="run-1",
    )

    trace = store.list_recent(run_id="run-1")[0]
    assert response.text == "short response"
    assert trace.total_tokens > 0
    assert trace.tokens_estimated is True
