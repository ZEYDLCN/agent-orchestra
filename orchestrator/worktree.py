"""Creates/removes isolated git worktrees for workers against the target repo.

target_repo/ is a generated, git-ignored working copy (its own nested .git),
bootstrapped from the tracked target_repo_seed/ so it never ends up committed
as an embedded repo inside this tool's own repo.
"""
import shutil
import subprocess
from pathlib import Path


def ensure_target_repo(target_repo_path: Path, seed_path: Path) -> None:
    """Make sure target_repo is a git repo with at least one commit
    (git worktree requires that)."""
    git_dir = target_repo_path / ".git"
    if git_dir.exists():
        return
    target_repo_path.mkdir(parents=True, exist_ok=True)
    if seed_path.exists():
        shutil.copytree(seed_path, target_repo_path, dirs_exist_ok=True)
    subprocess.run(["git", "init"], cwd=target_repo_path, check=True, capture_output=True)
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


def remove_worktree(target_repo_path: Path, worktree_path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=target_repo_path, check=False, capture_output=True,
    )
