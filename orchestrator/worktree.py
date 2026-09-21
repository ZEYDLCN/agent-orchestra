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


def _git(repo_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Runs git against target_repo with safe.directory scoped to *this
    invocation* via -c, instead of writing to the user's global gitconfig.
    target_repo can end up owned by a different account than the one
    running the orchestrator (a container's fixed uid, a different local
    user or tool on a shared machine) -- git's "dubious ownership" guard
    would otherwise refuse every command against it, and this repo is
    disposable and fully regenerated from target_repo_seed, so trusting it
    is safe. A prior version did this with `git config --global --add
    safe.directory`, which mutates shared state on the host outside this
    app's own files and never gets cleaned up; -c leaves no trace."""
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_path}", *args],
        cwd=repo_path, check=check, capture_output=True, text=True,
    )


def ensure_target_repo(target_repo_path: Path, seed_path: Path) -> None:
    """Make sure target_repo is a git repo with at least one commit
    (git worktree requires that)."""
    git_dir = target_repo_path / ".git"
    if git_dir.exists():
        return
    if not seed_path.exists():
        raise SeedMissingError(
            f"target_repo_seed not found at {seed_path} -- nothing to bootstrap "
            "target_repo from, tasks would fail at runtime instead of at startup"
        )
    target_repo_path.mkdir(parents=True, exist_ok=True)
    shutil.copytree(seed_path, target_repo_path, dirs_exist_ok=True)
    _git(target_repo_path, "init")
    _git(target_repo_path, "config", "user.email", "orchestrator@local")
    _git(target_repo_path, "config", "user.name", "orchestrator")
    _git(target_repo_path, "add", "-A")
    _git(target_repo_path, "commit", "-m", "initial commit", "--allow-empty")


def add_worktree(target_repo_path: Path, worktree_path: Path, branch: str) -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _git(target_repo_path, "worktree", "add", "-B", branch, str(worktree_path))


def remove_worktree(target_repo_path: Path, worktree_path: Path, branch: str) -> None:
    result = _git(target_repo_path, "worktree", "remove", "--force", str(worktree_path), check=False)
    if result.returncode != 0:
        logger.warning("failed to remove worktree %s: %s", worktree_path, result.stderr.strip())
    branch_result = _git(target_repo_path, "branch", "-D", branch, check=False)
    if branch_result.returncode != 0:
        logger.warning("failed to delete branch %s: %s", branch, branch_result.stderr.strip())
    prune_result = _git(target_repo_path, "worktree", "prune", check=False)
    if prune_result.returncode != 0:
        logger.warning("git worktree prune failed: %s", prune_result.stderr.strip())
