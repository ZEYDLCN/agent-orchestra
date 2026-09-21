from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    target_repo_seed_path: Path = REPO_ROOT / "target_repo_seed"
    target_repo_path: Path = REPO_ROOT / "target_repo"
    workspace_path: Path = REPO_ROOT / "workspace" / "workers"

    # multi-LLM: "mock" (default, no key needed), "anthropic", or "openai"
    llm_provider: str = "mock"
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"

    # shared memory / RAG-lite
    memory_top_k: int = 3

    orchestrator_base_url: str = "http://localhost:8000"

    model_config = SettingsConfigDict(env_prefix="ORCH_")


settings = Settings()
