import subprocess
import sys
import uuid
from pathlib import Path

from orchestrator.config import settings
from orchestrator.models import WorkerInfo
from orchestrator.worktree import add_worktree, ensure_target_repo, remove_worktree


class WorkerManager:
    def __init__(self):
        self._workers: dict[str, tuple[WorkerInfo, subprocess.Popen]] = {}
        ensure_target_repo(settings.target_repo_path, settings.target_repo_seed_path)

    def spawn_worker(self) -> WorkerInfo:
        worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        branch = f"agent/{worker_id}"
        worktree_path = settings.workspace_path / worker_id

        add_worktree(settings.target_repo_path, worktree_path, branch)

        proc = subprocess.Popen(
            [
                sys.executable, "-m", "worker.run_worker",
                "--worker-id", worker_id,
                "--workdir", str(worktree_path),
            ],
            cwd=str(Path(__file__).resolve().parent.parent),
        )

        info = WorkerInfo(
            worker_id=worker_id,
            pid=proc.pid,
            worktree_path=str(worktree_path),
            branch=branch,
        )
        self._workers[worker_id] = (info, proc)
        return info

    def scale_to(self, count: int) -> list[WorkerInfo]:
        current = len(self._workers)
        for _ in range(max(0, count - current)):
            self.spawn_worker()
        return self.list_workers()

    def list_workers(self) -> list[WorkerInfo]:
        result = []
        for worker_id, (info, proc) in self._workers.items():
            info.status = "running" if proc.poll() is None else "stopped"
            result.append(info)
        return result

    def stop_worker(self, worker_id: str) -> bool:
        entry = self._workers.pop(worker_id, None)
        if entry is None:
            return False
        info, proc = entry
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        remove_worktree(settings.target_repo_path, Path(info.worktree_path))
        return True

    def stop_all(self) -> None:
        for worker_id in list(self._workers.keys()):
            self.stop_worker(worker_id)
