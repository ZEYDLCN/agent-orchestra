"""Goal-driven planner -> traders -> reviewer orchestration loop."""

import json
import logging
import threading
import time
from typing import Any, cast

from orchestrator.config import settings
from orchestrator.coordinator import propose_refinement
from orchestrator.llm.base import LLMProvider
from orchestrator.messaging import MessageBus
from orchestrator.models import AgentRun, AgentRunRequest, AgentRunStatus, Task, TaskStatus
from orchestrator.queue import TaskQueue
from orchestrator.redis_client import make_redis_client
from orchestrator.tracing import TraceStore, traced_generate

logger = logging.getLogger(__name__)

RUN_INDEX_KEY = "agent_runs:index"


def _run_key(run_id: str) -> str:
    return f"agent_run:{run_id}"


def _extract_object(text: str) -> dict[str, Any] | None:
    """Accept plain or fenced JSON while rejecting free-text lookalikes."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else text
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def _normalize_tasks(raw_tasks: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(raw_tasks, list):
        return []
    payloads: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        params = item.get("params", item)
        if not isinstance(params, dict):
            continue
        try:
            fast = int(params["fast_ma"])
            slow = int(params["slow_ma"])
        except (KeyError, TypeError, ValueError):
            continue
        if fast <= 0 or slow <= 0 or fast >= slow or (fast, slow) in seen:
            continue
        seen.add((fast, slow))
        payloads.append({"params": {"fast_ma": fast, "slow_ma": slow}})
        if len(payloads) >= limit:
            break
    return payloads


def _default_plan(limit: int) -> list[dict[str, Any]]:
    tasks = [
        {"params": {"fast_ma": fast, "slow_ma": slow}}
        for fast in (5, 10, 20)
        for slow in (50, 100, 200)
        if fast < slow
    ]
    return tasks[:limit]


class PlannerAgent:
    role = "planner"

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def plan(
        self,
        goal: str,
        max_tasks: int,
        trace_store: TraceStore | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        prompt = f"""You are the Planner Agent in a trading experiment system.
Turn the user goal into at most {max_tasks} backtest tasks.
Each task must contain positive integer fast_ma and slow_ma, with fast_ma < slow_ma.
Return JSON only, exactly in this shape:
{{"summary":"short plan","tasks":[{{"params":{{"fast_ma":5,"slow_ma":50}}}}]}}
User goal: {goal}"""
        response = traced_generate(
            self.llm,
            prompt,
            trace_store,
            role=self.role,
            agent_id="planner",
            run_id=run_id,
        )
        raw = response.text
        parsed = _extract_object(raw)
        tasks = _normalize_tasks(parsed.get("tasks") if parsed else None, max_tasks)
        fallback = not tasks
        if fallback:
            tasks = _default_plan(max_tasks)
        return {
            "role": self.role,
            "provider": self.llm.name,
            "summary": str(parsed.get("summary", ""))[:500]
            if parsed
            else "Varsayılan güvenli parametre taraması oluşturuldu.",
            "tasks": tasks,
            "fallback": fallback,
            "raw_response": raw[:4000],
            "llm_usage": response.usage.as_dict(),
        }


class ReviewerAgent:
    role = "reviewer"

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def review(
        self,
        goal: str,
        tasks: list[Task],
        round_number: int,
        max_rounds: int,
        max_tasks: int,
        trace_store: TraceStore | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        scored = sorted(
            [task.result for task in tasks if task.status == TaskStatus.DONE and task.result],
            key=lambda result: result.get("score", float("-inf")),
            reverse=True,
        )
        prompt = f"""You are the Reviewer Agent in a multi-agent trading experiment.
Review the trader results against the user goal. Current round is {round_number}/{max_rounds}.
Choose "refine" only when another round can materially improve the search; otherwise choose "final".
If refining, propose at most {max_tasks} valid fast_ma/slow_ma tasks.
Return JSON only:
{{"decision":"final|refine","summary":"clear Turkish conclusion","tasks":[{{"params":{{"fast_ma":8,"slow_ma":80}}}}]}}
User goal: {goal}
Results: {json.dumps(scored[:8], ensure_ascii=False)}"""
        response = traced_generate(
            self.llm,
            prompt,
            trace_store,
            role=self.role,
            agent_id="reviewer",
            run_id=run_id,
            job_id=job_id,
            round_number=round_number,
        )
        raw = response.text
        parsed = _extract_object(raw)
        decision = str(parsed.get("decision", "") if parsed else "").lower()
        proposed = _normalize_tasks(parsed.get("tasks") if parsed else None, max_tasks)
        fallback = decision not in {"final", "refine"}
        if round_number >= max_rounds:
            decision = "final"
        elif fallback:
            decision = "refine"
        if decision == "refine" and not proposed:
            proposed = propose_refinement(tasks)[:max_tasks]
            fallback = True
        return {
            "role": self.role,
            "provider": self.llm.name,
            "decision": decision,
            "summary": str(parsed.get("summary", ""))[:1000]
            if parsed
            else "Sonuçlar değerlendirildi; güvenli fallback kararı uygulandı.",
            "tasks": proposed,
            "fallback": fallback,
            "raw_response": raw[:4000],
            "llm_usage": response.usage.as_dict(),
        }


class AgentWorkflow:
    def __init__(
        self,
        queue: TaskQueue,
        bus: MessageBus,
        planner: PlannerAgent,
        reviewer: ReviewerAgent,
        redis_url: str | None = None,
    ):
        self.queue = queue
        self.bus = bus
        self.planner = planner
        self.reviewer = reviewer
        self.client = make_redis_client(redis_url)
        self.trace_store = TraceStore(redis_url)

    def rebind_client(self, client) -> None:
        self.client = client
        self.trace_store.rebind_client(client)

    def start(self, request: AgentRunRequest) -> AgentRun:
        run = AgentRun(
            goal=request.goal,
            max_rounds=request.max_rounds,
            max_tasks_per_round=request.max_tasks_per_round,
            trader_count=request.trader_count,
        )
        self._save(run)
        threading.Thread(target=self._execute, args=(run.run_id,), daemon=True).start()
        return run

    def get(self, run_id: str) -> AgentRun | None:
        raw = cast(str | None, self.client.get(_run_key(run_id)))
        return AgentRun.model_validate_json(raw) if raw else None

    def list_recent(self, limit: int = 20) -> list[AgentRun]:
        ids = cast(list[str], self.client.zrevrange(RUN_INDEX_KEY, 0, limit - 1))
        return [run for run_id in ids if (run := self.get(run_id)) is not None]

    def _save(self, run: AgentRun) -> None:
        run.updated_at = time.time()
        pipe = self.client.pipeline()
        pipe.set(_run_key(run.run_id), run.model_dump_json(), ex=settings.task_ttl_seconds)
        pipe.zadd(RUN_INDEX_KEY, {run.run_id: run.created_at})
        pipe.zremrangebyrank(RUN_INDEX_KEY, 0, -settings.job_index_max_entries - 1)
        pipe.execute()

    def _event(self, run: AgentRun, event_type: str, data: dict[str, Any]) -> None:
        self.bus.publish(run.run_id, event_type, {"run_id": run.run_id, **data})

    def _submit_job(
        self, run: AgentRun, payloads: list[dict[str, Any]], parent_job_id: str | None
    ) -> str:
        import uuid

        job_id = uuid.uuid4().hex
        self.queue.register_job(
            job_id,
            meta={
                "job_id": job_id,
                "parent_job_id": parent_job_id,
                "kind": "agent_plan" if parent_job_id is None else "agent_refine",
                "required_capability": "backtest",
                "agent_run_id": run.run_id,
                "round": run.current_round,
                "goal": run.goal,
            },
        )
        for payload in payloads:
            self.queue.push_task(
                Task(
                    job_id=job_id,
                    payload={
                        **payload,
                        "_trace_context": {
                            "run_id": run.run_id,
                            "round_number": run.current_round,
                        },
                    },
                )
            )
        return job_id

    def _wait_for_job(self, job_id: str, deadline: float) -> list[Task]:
        while time.time() < deadline:
            tasks = self.queue.get_job_tasks(job_id)
            if tasks and all(
                task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
                for task in tasks
            ):
                return tasks
            time.sleep(settings.agent_run_poll_interval_seconds)
        raise TimeoutError(f"agent run timed out waiting for job {job_id}")

    def _execute(self, run_id: str) -> None:
        run = self.get(run_id)
        if run is None:
            return
        deadline = time.time() + settings.agent_run_timeout_seconds
        try:
            plan = self.planner.plan(
                run.goal,
                run.max_tasks_per_round,
                trace_store=self.trace_store,
                run_id=run.run_id,
            )
            run.plan = plan
            self._save(run)
            self._event(run, "planner_completed", plan)
            payloads = plan["tasks"]
            parent_job_id = None
            all_completed: list[dict[str, Any]] = []

            for round_number in range(1, run.max_rounds + 1):
                run.current_round = round_number
                run.status = AgentRunStatus.RUNNING
                run.stage = "traders"
                job_id = self._submit_job(run, payloads, parent_job_id)
                run.current_job_id = job_id
                run.job_ids.append(job_id)
                self._save(run)
                self._event(run, "trader_round_started", {"round": round_number, "job_id": job_id})

                tasks = self._wait_for_job(job_id, deadline)
                all_completed.extend(
                    task.result
                    for task in tasks
                    if task.status == TaskStatus.DONE and task.result is not None
                )
                run.status = AgentRunStatus.REVIEWING
                run.stage = "reviewer"
                self._save(run)
                review = self.reviewer.review(
                    run.goal,
                    tasks,
                    round_number,
                    run.max_rounds,
                    run.max_tasks_per_round,
                    trace_store=self.trace_store,
                    run_id=run.run_id,
                    job_id=job_id,
                )
                run.reviews.append(review)
                self._save(run)
                self._event(run, "reviewer_completed", {"round": round_number, **review})

                if review["decision"] == "final" or round_number >= run.max_rounds:
                    all_completed.sort(
                        key=lambda result: result.get("score", float("-inf")), reverse=True
                    )
                    run.final_result = {
                        "summary": review["summary"],
                        "best_result": all_completed[0] if all_completed else None,
                        "rounds": round_number,
                        "jobs": list(run.job_ids),
                    }
                    run.status = AgentRunStatus.COMPLETED
                    run.stage = "final"
                    self._save(run)
                    self._event(run, "agent_run_completed", run.final_result)
                    return

                payloads = review["tasks"]
                if not payloads:
                    raise RuntimeError("reviewer requested refinement without valid tasks")
                parent_job_id = job_id
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent run %s failed", run.run_id)
            run.status = AgentRunStatus.FAILED
            run.stage = "failed"
            run.error = str(exc)
            self._save(run)
            self._event(run, "agent_run_failed", {"error": run.error})
