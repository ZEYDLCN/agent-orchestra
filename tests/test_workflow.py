import time

import fakeredis

from orchestrator.config import settings
from orchestrator.messaging import MessageBus
from orchestrator.models import AgentRunRequest, AgentRunStatus, Task, TaskStatus
from orchestrator.queue import TaskQueue
from orchestrator.workflow import AgentWorkflow, PlannerAgent, ReviewerAgent


class StubLLM:
    name = "stub:model"

    def __init__(self, response: str):
        self.response = response

    def generate(self, prompt: str) -> str:
        return self.response


class SequenceLLM:
    name = "stub:model"

    def __init__(self, responses: list[str]):
        self.responses = iter(responses)

    def generate(self, prompt: str) -> str:
        return next(self.responses)


def test_planner_turns_goal_into_valid_bounded_tasks():
    planner = PlannerAgent(
        StubLLM(
            '```json\n{"summary":"plan","tasks":['
            '{"params":{"fast_ma":5,"slow_ma":50}},'
            '{"params":{"fast_ma":10,"slow_ma":100}},'
            '{"params":{"fast_ma":100,"slow_ma":10}}]}\n```'
        )
    )

    plan = planner.plan("en iyi hareketli ortalama parametrelerini bul", max_tasks=2)

    assert plan["fallback"] is False
    assert plan["tasks"] == [
        {"params": {"fast_ma": 5, "slow_ma": 50}},
        {"params": {"fast_ma": 10, "slow_ma": 100}},
    ]


def test_planner_uses_safe_grid_when_llm_does_not_return_json():
    plan = PlannerAgent(StubLLM("plain text")).plan("goal", max_tasks=3)

    assert plan["fallback"] is True
    assert len(plan["tasks"]) == 3


def test_reviewer_forces_final_at_round_limit():
    task = Task(job_id="job-1", payload={"params": {"fast_ma": 5, "slow_ma": 50}})
    task.status = TaskStatus.DONE
    task.result = {"score": 91.0, "params": task.payload["params"]}
    reviewer = ReviewerAgent(
        StubLLM(
            '{"decision":"refine","summary":"bir tur daha",'
            '"tasks":[{"params":{"fast_ma":6,"slow_ma":60}}]}'
        )
    )

    review = reviewer.review("goal", [task], round_number=2, max_rounds=2, max_tasks=5)

    assert review["decision"] == "final"


def test_complete_planner_trader_reviewer_loop(monkeypatch):
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    queue = TaskQueue()
    queue.rebind_client(client)
    bus = MessageBus()
    bus.client = client
    workflow = AgentWorkflow(
        queue,
        bus,
        PlannerAgent(
            StubLLM(
                '{"summary":"iki görev", "tasks":['
                '{"params":{"fast_ma":5,"slow_ma":50}},'
                '{"params":{"fast_ma":10,"slow_ma":100}}]}'
            )
        ),
        ReviewerAgent(StubLLM('{"decision":"final","summary":"tamamlandı","tasks":[]}')),
    )
    workflow.rebind_client(client)
    monkeypatch.setattr(settings, "agent_run_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "agent_run_timeout_seconds", 5)

    run = workflow.start(AgentRunRequest(goal="iki parametreyi karşılaştır", max_rounds=1))
    handled = 0
    deadline = time.time() + 5
    while handled < 2 and time.time() < deadline:
        task = queue.pop_task(["backtest"], "trader-1", timeout=1)
        if task is None:
            continue
        params = task.payload["params"]
        queue.ack(
            task.task_id,
            {"score": 90 + handled, "params": params, "llm_provider": "stub:trader"},
            task.lease_token,
        )
        handled += 1

    while time.time() < deadline:
        current = workflow.get(run.run_id)
        if current and current.status in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED):
            break
        time.sleep(0.01)

    assert current is not None
    assert current.status == AgentRunStatus.COMPLETED
    assert current.current_round == 1
    assert len(current.job_ids) == 1
    assert current.final_result["best_result"]["score"] == 91


def test_final_result_keeps_best_score_across_refinement_rounds(monkeypatch):
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    queue = TaskQueue()
    queue.rebind_client(client)
    bus = MessageBus()
    bus.client = client
    workflow = AgentWorkflow(
        queue,
        bus,
        PlannerAgent(
            StubLLM('{"summary":"ilk tur","tasks":[{"params":{"fast_ma":5,"slow_ma":50}}]}')
        ),
        ReviewerAgent(
            SequenceLLM(
                [
                    '{"decision":"refine","summary":"daralt",'
                    '"tasks":[{"params":{"fast_ma":6,"slow_ma":40}}]}',
                    '{"decision":"final","summary":"tamam","tasks":[]}',
                ]
            )
        ),
    )
    workflow.rebind_client(client)
    monkeypatch.setattr(settings, "agent_run_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "agent_run_timeout_seconds", 5)

    run = workflow.start(AgentRunRequest(goal="iki turu karşılaştır", max_rounds=2))
    deadline = time.time() + 5
    for score in (95.0, 70.0):
        while time.time() < deadline:
            task = queue.pop_task(["backtest"], "trader-1", timeout=1)
            if task is not None:
                queue.ack(
                    task.task_id,
                    {"score": score, "params": task.payload["params"]},
                    task.lease_token,
                )
                break

    while time.time() < deadline:
        current = workflow.get(run.run_id)
        if current and current.status in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED):
            break
        time.sleep(0.01)

    assert current is not None
    assert current.status == AgentRunStatus.COMPLETED
    assert current.final_result["best_result"]["score"] == 95.0
