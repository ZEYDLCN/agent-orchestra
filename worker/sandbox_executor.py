"""Runs a task's strategy.py inside a locked-down, ephemeral Docker
container instead of importing it directly into the worker's own process.

A git worktree only isolates *files* -- code run inside it can still read
the host's env vars (API keys), hit the network, spawn processes, and
consume unbounded CPU/memory. The container closes all of that: no
network, read-only root fs, non-root user, resource caps, and killing the
container reliably kills every child process it spawned.
"""
import json
import subprocess
from pathlib import Path

from orchestrator.config import settings


class SandboxExecutionError(RuntimeError):
    pass


_docker_available_cache: bool | None = None


def is_docker_available(force_recheck: bool = False) -> bool:
    """Cached per-process: checking `docker info` on every single task
    would add ~100-300ms of pure overhead to each one."""
    global _docker_available_cache
    if _docker_available_cache is None or force_recheck:
        try:
            subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
            _docker_available_cache = True
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            _docker_available_cache = False
    return _docker_available_cache


def run_in_sandbox(workdir: Path, params: dict) -> dict:
    cmd = [
        "docker", "run", "--rm",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:size=16m,mode=1777",
        "--memory", f"{settings.sandbox_memory_mb}m",
        "--cpus", str(settings.sandbox_cpus),
        "--pids-limit", str(settings.sandbox_pids_limit),
        "--user", "1000:1000",
        "--security-opt", "no-new-privileges",
        "-v", f"{workdir.resolve()}:/workspace:ro",
        settings.sandbox_image,
        json.dumps(params),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=settings.sandbox_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxExecutionError(
            f"sandbox execution timed out after {settings.sandbox_timeout_seconds}s"
        ) from exc

    stdout = proc.stdout[: settings.sandbox_output_limit_bytes]
    stderr = proc.stderr[: settings.sandbox_output_limit_bytes]

    if proc.returncode != 0:
        raise SandboxExecutionError(f"sandbox exited {proc.returncode}: {(stderr or stdout).strip()[:500]}")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SandboxExecutionError(f"sandbox produced non-JSON output: {stdout[:500]!r}") from exc

    if "error" in payload:
        raise SandboxExecutionError(str(payload["error"])[:500])
    return payload
