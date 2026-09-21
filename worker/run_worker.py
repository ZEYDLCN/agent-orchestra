"""Worker entrypoint: pulls tasks from the Redis queue and executes them
inside its own git-worktree-isolated workdir, using shared memory, an LLM
provider, and the inter-agent message bus.

Registers itself in the Redis worker registry and heartbeats on a
background thread; a crash or hard kill just lets the registry TTL expire
and lets the queue's lease reaper reclaim whatever task it was holding.
A graceful stop (DELETE /workers/{id}) is cooperative: the orchestrator
sets a stop flag in Redis, and this loop checks it between tasks --
before claiming a new one, never mid-task -- and exits without claiming
further work. No OS signal is required, which matters because Windows'
Popen.terminate() has no graceful hook. The task it's currently holding
(if any) still runs to completion and is ack'd/nack'd normally; the
orchestrator's stop-wait window is sized to comfortably outlast a task's
lease so it doesn't fall back to a hard kill while legitimate work is
still in flight (see worker_manager.stop_worker).
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


def lease_renewal_loop(queue: TaskQueue, task_id: str, lease_token: str, stop_event: threading.Event):
    """Keeps a slow-but-alive task's lease from expiring mid-run (a slow
    LLM call, a slow sandbox execution) -- without this, task_lease_seconds
    is a hard ceiling on task duration regardless of whether the worker is
    actually still making progress."""
    interval = settings.task_lease_seconds / 3
    while not stop_event.wait(interval):
        queue.renew_lease(task_id, lease_token)


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

            renewal_stop = threading.Event()
            renewal_thread = threading.Thread(
                target=lease_renewal_loop,
                args=(queue, task.task_id, task.lease_token, renewal_stop),
                daemon=True,
            )
            renewal_thread.start()
            try:
                result = run_task(task, workdir_path, worker_id, llm, memory, bus)
                if queue.ack(task.task_id, result, lease_token=task.lease_token):
                    logger.info("task done score=%s", result.get("score"), extra=log_ctx)
                else:
                    logger.warning("ack dropped: lease was reclaimed while running", extra=log_ctx)
            except Exception as exc:  # noqa: BLE001
                if queue.nack(task.task_id, str(exc), lease_token=task.lease_token):
                    logger.warning("task failed: %s", exc, extra=log_ctx)
                else:
                    logger.warning("nack dropped: lease was reclaimed while running", extra=log_ctx)
            finally:
                renewal_stop.set()
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
