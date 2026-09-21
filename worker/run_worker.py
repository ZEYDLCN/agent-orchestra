"""Worker entrypoint: pulls tasks from the Redis queue and executes them
inside its own git-worktree-isolated workdir, using shared memory, an LLM
provider, and the inter-agent message bus."""
import argparse
import sys
from pathlib import Path

from orchestrator.config import settings
from orchestrator.llm.factory import get_llm_provider
from orchestrator.memory import SharedMemory
from orchestrator.messaging import MessageBus
from orchestrator.queue import TaskQueue
from worker.executor import run_task


def main(worker_id: str, workdir: str):
    workdir_path = Path(workdir)
    queue = TaskQueue(settings.redis_url)
    memory = SharedMemory(settings.redis_url)
    bus = MessageBus(settings.redis_url)
    llm = get_llm_provider()

    print(f"[{worker_id}] started, workdir={workdir_path}, llm={llm.name}", flush=True)

    while True:
        task = queue.pop_task(timeout=2)
        if task is None:
            continue
        queue.mark_running(task.task_id, worker_id)
        print(f"[{worker_id}] running task {task.task_id}", flush=True)
        try:
            result = run_task(task, workdir_path, worker_id, llm, memory, bus)
            queue.mark_done(task.task_id, result)
            print(f"[{worker_id}] done task {task.task_id} -> {result}", flush=True)
        except Exception as exc:  # noqa: BLE001
            queue.mark_failed(task.task_id, str(exc))
            print(f"[{worker_id}] failed task {task.task_id}: {exc}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    try:
        main(args.worker_id, args.workdir)
    except KeyboardInterrupt:
        sys.exit(0)
