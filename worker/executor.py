"""Runs a task's payload against the strategy module living in the worker's
own isolated worktree, then asks an LLM to comment on the result using
similar past runs retrieved from shared memory, and broadcasts the outcome
to peer workers over the message bus."""
import importlib.util
import time
from pathlib import Path

from orchestrator.llm.base import LLMProvider
from orchestrator.memory import SharedMemory
from orchestrator.messaging import MessageBus
from orchestrator.models import Task


def _load_strategy(workdir: Path):
    strategy_path = workdir / "strategy.py"
    spec = importlib.util.spec_from_file_location("strategy", strategy_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    strategy = _load_strategy(workdir)
    params = task.payload.get("params", {})
    score = strategy.backtest(params)

    similar = memory.retrieve_similar(params, top_k=3)
    commentary = llm.generate(_build_prompt(params, score, similar))

    duration_ms = (time.perf_counter() - start) * 1000
    result = {
        "worker_id": worker_id,
        "params": params,
        "score": score,
        "commentary": commentary,
        "llm_provider": llm.name,
        "duration_ms": round(duration_ms, 2),
    }

    memory.record({"params": params, "score": score, "worker_id": worker_id})
    bus.publish(task.job_id, "task_result", result)

    return result
