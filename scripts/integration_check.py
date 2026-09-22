"""End-to-end integration check against a *real* Redis and a *real*
sandbox Docker image -- everything tests/ covers with fakeredis, this
covers with the actual systems, including the one failure mode that
can't be faked: a worker process actually dying mid-task.

Starts its own orchestrator subprocess, so it's self-contained. Requires:
  - a reachable Redis at ORCH_REDIS_URL (default redis://localhost:6379/0)
  - the sandbox image built: docker build -t agent-orchestrator-sandbox:latest ./sandbox
  - docker on PATH and reachable

Usage:
    python scripts/integration_check.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8123"


def wait_for(predicate, timeout: float, interval: float = 0.5, description: str = "condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for: {description}")


def main() -> int:
    env = os.environ.copy()
    env.setdefault("ORCH_REDIS_URL", "redis://localhost:6379/0")
    env["ORCH_TASK_LEASE_SECONDS"] = "5"
    env["ORCH_REAPER_INTERVAL_SECONDS"] = "2"

    print("-> starting orchestrator subprocess")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "orchestrator.main:app", "--port", "8123"],
        cwd=str(REPO_ROOT), env=env,
    )
    try:
        wait_for(
            lambda: _health_ok(), timeout=20, description="orchestrator /health"
        )
        print("-> orchestrator is up")

        ready = requests.get(f"{BASE_URL}/ready").json()
        print("-> readiness:", ready)
        assert ready["checks"]["redis"], "Redis check failed -- is Redis reachable?"

        print("-> scaling to 2 workers")
        workers = requests.post(f"{BASE_URL}/workers/scale", json={"count": 2}).json()
        assert len(workers) == 2, f"expected 2 workers, got {workers}"
        victim = workers[0]

        grid = [{"params": {"fast_ma": f, "slow_ma": 100}} for f in range(1, 21)]
        print(f"-> submitting {len(grid)} tasks")
        job = requests.post(f"{BASE_URL}/jobs", json={"tasks": grid}).json()
        job_id = job["job_id"]

        time.sleep(1.5)
        print(f"-> hard-killing worker {victim['worker_id']} (pid={victim['pid']}) mid-job")
        _hard_kill(victim["pid"])

        def job_finished():
            status = requests.get(f"{BASE_URL}/jobs/{job_id}").json()
            return status["pending_or_running"] == 0

        wait_for(job_finished, timeout=90, description="job completion after worker crash")

        final = requests.get(f"{BASE_URL}/jobs/{job_id}").json()
        print(f"-> final: done={final['done']} failed={final['failed']}")
        assert final["done"] == len(grid), f"expected {len(grid)} done, got {final['done']}"
        assert final["failed"] == 0, f"expected 0 failed, got {final['failed']}"

        print("-> scaling back to 2 to confirm the dead worker gets replaced")
        workers_after = requests.post(f"{BASE_URL}/workers/scale", json={"count": 2}).json()
        assert len(workers_after) == 2, f"expected dead worker replaced, got {workers_after}"

        for w in workers_after:
            requests.delete(f"{BASE_URL}/workers/{w['worker_id']}")

        print("\nPASS: job survived a hard-killed worker with zero data loss")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _health_ok() -> bool:
    try:
        return requests.get(f"{BASE_URL}/health", timeout=2).status_code == 200
    except requests.RequestException:
        return False


def _hard_kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["powershell", "-Command", f"Stop-Process -Id {pid} -Force"], check=True)
    else:
        os.kill(pid, 9)


if __name__ == "__main__":
    sys.exit(main())
