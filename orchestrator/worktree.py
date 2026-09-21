"""Creates/removes isolated git worktrees for workers against the target repo.

target_repo/ is a generated, git-ignored working copy (its own nested .git),
bootstrapped from the tracked target_repo_seed/ so it never ends up committed
as an embedded repo inside this tool's own repo.
"""
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GitNotAvailableError(RuntimeError):
    pass


class SeedMissingError(RuntimeError):
    pass


def check_git_available() -> None:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GitNotAvailableError(
            "git is not installed or not on PATH -- required to isolate worker worktrees"
        ) from exc


def _trust_target_repo(target_repo_path: Path) -> None:
    # target_repo can end up owned by a different account than the one
    # running the orchestrator (a container's fixed uid, a different local
    # user or tool on a shared machine) -- git's "dubious ownership" guard
    # would otherwise refuse every command against it. This repo is
    # disposable and fully regenerated from target_repo_seed, so there's
    # no safety tradeoff in trusting it unconditionally. Idempotent and
    # cheap, so it runs on every startup, not just first creation --
    # someone/something else may have created target_repo under a
    # different account than the one running right now.
    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", str(target_repo_path)],
        check=True, capture_output=True,
    )


def ensure_target_repo(target_repo_path: Path, seed_path: Path) -> None:
    """Make sure target_repo is a git repo with at least one commit
    (git worktree requires that)."""
    git_dir = target_repo_path / ".git"
    if git_dir.exists():
        _trust_target_repo(target_repo_path)
        return
    if not seed_path.exists():
        raise SeedMissingError(
            f"target_repo_seed not found at {seed_path} -- nothing to bootstrap "
            "target_repo from, tasks would fail at runtime instead of at startup"
        )
    target_repo_path.mkdir(parents=True, exist_ok=True)
    shutil.copytree(seed_path, target_repo_path, dirs_exist_ok=True)
    subprocess.run(["git", "init"], cwd=target_repo_path, check=True, capture_output=True)
    _trust_target_repo(target_repo_path)
    subprocess.run(
        ["git", "config", "user.email", "orchestrator@local"],
        cwd=target_repo_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "orchestrator"],
        cwd=target_repo_path, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=target_repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit", "--allow-empty"],
        cwd=target_repo_path, check=True, capture_output=True,
    )


def add_worktree(target_repo_path: Path, worktree_path: Path, branch: str) -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "-B", branch, str(worktree_path)],
        cwd=target_repo_path, check=True, capture_output=True,
    )


def remove_worktree(target_repo_path: Path, worktree_path: Path, branch: str) -> None:
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=target_repo_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning(
            "failed to remove worktree %s: %s", worktree_path, result.stderr.strip()
        )
    branch_result = subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=target_repo_path, capture_output=True, text=True,
    )
    if branch_result.returncode != 0:
        logger.warning("failed to delete branch %s: %s", branch, branch_result.stderr.strip())
    prune_result = subprocess.run(
        ["git", "worktree", "prune"], cwd=target_repo_path, capture_output=True, text=True,
    )
    if prune_result.returncode != 0:
        logger.warning("git worktree prune failed: %s", prune_result.stderr.strip())
