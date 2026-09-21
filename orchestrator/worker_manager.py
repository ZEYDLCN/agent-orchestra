import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from orchestrator.config import settings
from orchestrator.models import WorkerInfo
from orchestrator.worker_registry import WorkerRegistry
from orchestrator.worktree import (
    add_worktree,
    check_git_available,
    ensure_target_repo,
    remove_worktree,
)

SCALE_LOCK_KEY = "lock:worker_scale"


class WorkerManager:
    """Owns *local* worker processes: creating their git worktree, spawning
    the subprocess, and asking them to stop. Worker *visibility* (the list
    the dashboard reads) comes from WorkerRegistry, which is Redis-backed
    and therefore also reflects workers spawned by other orchestrator
    processes or manually on remote machines."""

    def __init__(self):
        self._local: dict[str, tuple[WorkerInfo, subprocess.Popen]] = {}
        self._local_lock = threading.Lock()
        self.registry = WorkerRegistry(settings.redis_url)
        check_git_available()
        ensure_target_repo(settings.target_repo_path, settings.target_repo_seed_path)

    def spawn_worker(self) -> WorkerInfo:
        worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        branch = f"agent/{worker_id}"
        worktree_path = settings.workspace_path / worker_id

        add_worktree(settings.target_repo_path, worktree_path, branch)

        try:
            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "worker.run_worker",
                    "--worker-id", worker_id,
                    "--workdir", str(worktree_path),
                ],
                cwd=str(Path(__file__).resolve().parent.parent),
            )
        except OSError:
            remove_worktree(settings.target_repo_path, worktree_path, branch)
            raise

        info = WorkerInfo(
            worker_id=worker_id,
            pid=proc.pid,
            hostname=socket.gethostname(),
            worktree_path=str(worktree_path),
            branch=branch,
            managed_locally=True,
        )
        with self._local_lock:
            self._local[worker_id] = (info, proc)

        # bounded wait for the worker's first heartbeat, so callers
        # (POST /workers/scale's response, the dashboard's immediate
        # render from it) see it right away instead of a briefly-empty
        # list until the next periodic /workers poll catches up
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.registry.get(worker_id) is not None:
                break
            time.sleep(0.05)

        return info

    def _reap_dead_local(self) -> None:
        """A worker we spawned can die without going through stop_worker
        (crash, hard kill, OOM) -- it just stops heartbeating and its
        registry entry ages out via TTL, but our own Popen handle for it
        sits in self._local forever unless we notice. Left unreaped, it
        would make scale_to() think that dead worker still counts toward
        the target and refuse to replace it."""
        with self._local_lock:
            dead = [
                (worker_id, info)
                for worker_id, (info, proc) in self._local.items()
                if proc.poll() is not None
            ]
            for worker_id, _ in dead:
                del self._local[worker_id]
        for worker_id, info in dead:
            remove_worktree(settings.target_repo_path, Path(info.worktree_path), info.branch)
            self.registry.deregister(worker_id)

    def scale_to(self, count: int) -> list[WorkerInfo]:
        # a Redis lock serializes concurrent scale requests across threads
        # *and* across multiple orchestrator processes, so two racing
        # requests can't both see "current=2" and each spawn up to target
        # independently, overshooting the requested count.
        got_lock = self.registry.client.set(SCALE_LOCK_KEY, "1", nx=True, ex=30)
        if not got_lock:
            raise RuntimeError("another scale operation is already in progress")
        try:
            self._reap_dead_local()
            current = len(self.list_local_workers())
            for _ in range(max(0, count - current)):
                self.spawn_worker()
        finally:
            self.registry.client.delete(SCALE_LOCK_KEY)
        return self.list_workers()

    def list_local_workers(self) -> list[WorkerInfo]:
        self._reap_dead_local()
        with self._local_lock:
            entries = list(self._local.items())
        result = []
        for _worker_id, (info, proc) in entries:
            info.status = "running" if proc.poll() is None else "stopped"
            result.append(info)
        return result

    def list_workers(self) -> list[WorkerInfo]:
        """Full fleet, local and remote: the Redis registry is the source
        of truth since workers heartbeat into it themselves."""
        registry_workers = {w.worker_id: w for w in self.registry.list_all()}
        with self._local_lock:
            local_ids = set(self._local.keys())
        for worker_id in registry_workers:
            registry_workers[worker_id].managed_locally = worker_id in local_ids
        return list(registry_workers.values())

    def stop_worker(self, worker_id: str) -> bool:
        exists = self.registry.get(worker_id) is not None
        with self._local_lock:
            is_local = worker_id in self._local
        if not exists and not is_local:
            return False

        # cooperative: ask the worker to finish/release its current task
        # and exit on its own, so an in-flight task isn't just abandoned
        self.registry.request_stop(worker_id)

        deadline = time.time() + settings.worker_stop_grace_seconds
        while time.time() < deadline:
            if self.registry.get(worker_id) is None:
                break
            time.sleep(0.3)

        with self._local_lock:
            entry = self._local.pop(worker_id, None)
        if entry is not None:
            info, proc = entry
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            # only meaningful for workers this process spawned: their
            # worktree lives under our local target_repo. A remote worker
            # manages its own worktree on its own machine.
            remove_worktree(settings.target_repo_path, Path(info.worktree_path), info.branch)
        self.registry.deregister(worker_id)
        return True

    def stop_all(self) -> None:
        with self._local_lock:
            worker_ids = list(self._local.keys())
        for worker_id in worker_ids:
            self.stop_worker(worker_id)
