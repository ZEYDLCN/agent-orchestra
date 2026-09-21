"""Day-1 demo: scale up N workers, submit a parameter grid as a job,
poll until done, print results sorted by score.

Usage:
    python scripts/demo.py --workers 5 --base-url http://localhost:8000
"""
import argparse
import time

import requests


def main(base_url: str, worker_count: int):
    print(f"-> scaling to {worker_count} workers")
    resp = requests.post(f"{base_url}/workers/scale", json={"count": worker_count})
    resp.raise_for_status()
    for w in resp.json():
        print(f"   {w['worker_id']} pid={w['pid']} worktree={w['worktree_path']}")

    grid = [
        {"params": {"fast_ma": fast, "slow_ma": slow}}
        for fast in (5, 10, 20)
        for slow in (50, 100, 200)
    ]
    print(f"-> submitting job with {len(grid)} tasks (param grid sweep)")
    resp = requests.post(f"{base_url}/jobs", json={"tasks": grid})
    resp.raise_for_status()
    job = resp.json()
    job_id = job["job_id"]
    print(f"   job_id={job_id}")

    print("-> waiting for completion")
    while True:
        resp = requests.get(f"{base_url}/jobs/{job_id}")
        resp.raise_for_status()
        status = resp.json()
        print(
            f"   done={status['done']} failed={status['failed']} "
            f"pending/running={status['pending_or_running']}"
        )
        if status["pending_or_running"] == 0:
            break
        time.sleep(1)

    results = [t for t in status["tasks"] if t["status"] == "done"]
    results.sort(key=lambda t: t["result"]["score"], reverse=True)

    print("\n-> top results:")
    for t in results[:5]:
        r = t["result"]
        print(
            f"   score={r['score']:>8} params={r['params']} "
            f"worker={r['worker_id']} ({r['duration_ms']}ms)"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    main(args.base_url, args.workers)
