from pathlib import Path

from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    target_repo_seed_path: Path = REPO_ROOT / "target_repo_seed"
    target_repo_path: Path = REPO_ROOT / "target_repo"
    workspace_path: Path = REPO_ROOT / "workspace" / "workers"

    class Config:
        env_prefix = "ORCH_"


settings = Settings()
