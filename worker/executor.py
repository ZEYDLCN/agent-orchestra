"""Runs a task's strategy -- sandboxed by default (see sandbox_executor.py)
-- then asks an LLM to comment on the result using similar past runs
retrieved from shared memory, and broadcasts the outcome to peer workers
over the message bus.

Strategy execution and LLM commentary are independent failure modes and
are tracked separately: a backtest that succeeds but whose commentary call
times out is still a successful task (execution_status=completed), not a
failed one. Only the strategy execution itself -- the untrusted, sandboxed
step -- is a "real" task failure that should trigger a queue retry.
"""
import logging
import time
from pathlib import Path

from orchestrator.config import settings
from orchestrator.llm.base import LLMProvider
from orchestrator.memory import SharedMemory
from orchestrator.messaging import MessageBus
from orchestrator.models import Task
from worker.local_executor import run_local
from worker.sandbox_executor import SandboxExecutionError, is_docker_available, run_in_sandbox

logger = logging.getLogger(__name__)


def _execute_strategy(workdir: Path, params: dict) -> dict:
    if settings.sandbox_enabled and is_docker_available():
        return run_in_sandbox(workdir, params)
    if settings.environment == "production":
        raise SandboxExecutionError(
            "sandbox disabled or Docker unavailable in production -- refusing "
            "to execute untrusted strategy code unsandboxed"
        )
    logger.warning("sandbox unavailable, running strategy unsandboxed (development only)")
    return run_local(workdir, params)


def _build_prompt(params: dict, score: float, similar: list[dict]) -> str:
    lines = [
        "Bir trading strateji backtest sonucunu tek cumlede degerlendir.",
        f"Parametreler: {params}",
        f"Skor: {score}",
    ]
    if similar:
        lines.append("Gecmiste denenen benzer parametreler:")
        for entry in similar:
            lines.append(f"  - {entry['params']} -> skor {entry['score']}")
    return "\n".join(lines)


def run_task(
    task: Task,
    workdir: Path,
    worker_id: str,
    llm: LLMProvider,
    memory: SharedMemory,
    bus: MessageBus,
) -> dict:
    start = time.perf_counter()
    params = task.payload.get("params", {})

    # untrusted step: failure here is a real task failure and propagates
    # so the queue retries/dead-letters it (see orchestrator/queue.py)
    execution = _execute_strategy(workdir, params)
    score = execution["score"]

    similar = memory.retrieve_similar(params, top_k=settings.memory_top_k)

    analysis_status = "completed"
    commentary = None
    try:
        commentary = llm.generate(_build_prompt(params, score, similar))
    except Exception as exc:  # noqa: BLE001
        analysis_status = "failed"
        logger.warning("LLM commentary failed for task %s: %s", task.task_id, exc)

    duration_ms = (time.perf_counter() - start) * 1000
    result = {
        "worker_id": worker_id,
        "params": params,
        "score": score,
        "execution_status": "completed",
        "analysis_status": analysis_status,
        "commentary": commentary,
        "llm_provider": llm.name,
        "duration_ms": round(duration_ms, 2),
    }

    memory.record({"params": params, "score": score, "worker_id": worker_id})
    bus.publish(task.job_id, "task_result", result)

    return result
