"""Worker entrypoint: pulls tasks from the Redis queue and executes them
inside its own git-worktree-isolated workdir, using shared memory, an LLM
provider, and the inter-agent message bus.

Registers itself in the Redis worker registry and heartbeats on a
background thread; a crash or hard kill just lets the registry TTL expire
and lets the queue's lease reaper reclaim whatever task it was holding.
A graceful stop (DELETE /workers/{id}) is cooperative: the orchestrator
sets a stop flag in Redis, this loop notices it between tasks, releases
its current task's lease if it's holding one, deregisters, and exits --
no OS signal is required, which matters because Windows' Popen.terminate()
has no graceful hook.
"""
import argparse
import logging
import os
import socket
import sys
import threading
from pathlib import Path

from orchestrator.config import settings
from orchestrator.llm.factory import get_llm_provider
from orchestrator.logging_setup import configure_logging
from orchestrator.memory import SharedMemory
from orchestrator.messaging import MessageBus
from orchestrator.models import WorkerInfo
from orchestrator.queue import TaskQueue
from orchestrator.worker_registry import WorkerRegistry
from worker.executor import run_task

CAPABILITIES = ["backtest"]

configure_logging()
logger = logging.getLogger("worker")


def heartbeat_loop(registry: WorkerRegistry, info: WorkerInfo, stop_event: threading.Event):
    while not stop_event.is_set():
        registry.heartbeat(info)
        stop_event.wait(settings.worker_heartbeat_interval_seconds)


def main(worker_id: str, workdir: str):
    workdir_path = Path(workdir)
    queue = TaskQueue(settings.redis_url)
    memory = SharedMemory(settings.redis_url)
    bus = MessageBus(settings.redis_url)
    registry = WorkerRegistry(settings.redis_url)
    llm = get_llm_provider()

    info = WorkerInfo(
        worker_id=worker_id,
        pid=os.getpid(),
        hostname=socket.gethostname(),
        worktree_path=str(workdir_path),
        branch=f"agent/{worker_id}",
        capabilities=CAPABILITIES,
        managed_locally=False,
    )
    registry.heartbeat(info)

    stop_event = threading.Event()
    hb_thread = threading.Thread(
        target=heartbeat_loop, args=(registry, info, stop_event), daemon=True
    )
    hb_thread.start()

    logger.info("worker started, llm=%s", llm.name, extra={"worker_id": worker_id})

    try:
        while True:
            if registry.is_stop_requested(worker_id):
                logger.info("stop requested, shutting down", extra={"worker_id": worker_id})
                break

            task = queue.pop_task(CAPABILITIES, worker_id, timeout=2)
            if task is None:
                continue

            info.current_task_id = task.task_id
            log_ctx = {"worker_id": worker_id, "job_id": task.job_id, "task_id": task.task_id, "attempt": task.attempt_count}
            logger.info("running task", extra=log_ctx)
            try:
                result = run_task(task, workdir_path, worker_id, llm, memory, bus)
                queue.ack(task.task_id, result)
                logger.info("task done score=%s", result.get("score"), extra=log_ctx)
            except Exception as exc:  # noqa: BLE001
                queue.nack(task.task_id, str(exc))
                logger.warning("task failed: %s", exc, extra=log_ctx)
            finally:
                info.current_task_id = None
    finally:
        stop_event.set()
        registry.deregister(worker_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    try:
        main(args.worker_id, args.workdir)
    except KeyboardInterrupt:
        sys.exit(0)
