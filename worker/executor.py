"""Runs a task's payload against the strategy module living in the worker's
own isolated worktree. Day-1 stand-in for real LLM-driven work: deterministic
so a demo run is reproducible."""
import importlib.util
import time
from pathlib import Path

from orchestrator.models import Task


def _load_strategy(workdir: Path):
    strategy_path = workdir / "strategy.py"
    spec = importlib.util.spec_from_file_location("strategy", strategy_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_task(task: Task, workdir: Path, worker_id: str) -> dict:
    start = time.perf_counter()
    strategy = _load_strategy(workdir)
    params = task.payload.get("params", {})
    score = strategy.backtest(params)
    duration_ms = (time.perf_counter() - start) * 1000
    return {
        "worker_id": worker_id,
        "params": params,
        "score": score,
        "duration_ms": round(duration_ms, 2),
    }
