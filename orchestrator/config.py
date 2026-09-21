from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    redis_connect_timeout: float = 5.0
    redis_socket_timeout: float = 5.0

    target_repo_seed_path: Path = REPO_ROOT / "target_repo_seed"
    target_repo_path: Path = REPO_ROOT / "target_repo"
    workspace_path: Path = REPO_ROOT / "workspace" / "workers"

    # "development" (default): missing LLM key silently falls back to the
    # mock provider. "production": missing/misconfigured provider fails
    # fast at startup instead of silently degrading.
    environment: str = "development"

    # multi-LLM: "mock" (default, no key needed), "anthropic", or "openai"
    llm_provider: str = "mock"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 20.0
    llm_max_attempts: int = 2

    # shared memory / RAG-lite
    memory_top_k: int = 3

    orchestrator_base_url: str = "http://localhost:8000"

    # durable queue: task lease (visibility timeout) + retry
    task_lease_seconds: int = 90
    task_max_attempts: int = 3
    reaper_interval_seconds: int = 15

    # worker registry (Redis-backed, not in-process)
    worker_heartbeat_interval_seconds: int = 5
    worker_ttl_seconds: int = 15
    worker_stop_grace_seconds: int = 10

    # Redis retention (self-cleaning via TTL, see queue.py/messaging.py)
    task_ttl_seconds: int = 24 * 3600
    job_index_max_entries: int = 200

    # sandboxed task execution (see worker/sandbox_executor.py)
    sandbox_enabled: bool = True
    sandbox_image: str = "agent-orchestrator-sandbox:latest"
    sandbox_timeout_seconds: int = 30
    sandbox_memory_mb: int = 256
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 64
    sandbox_output_limit_bytes: int = 65536

    # optional shared-secret auth; unset (default) keeps local/demo usage
    # frictionless. Set to require `X-API-Key` on mutating endpoints.
    api_key: str | None = None
    rate_limit_per_minute: int = 120

    model_config = SettingsConfigDict(env_prefix="ORCH_", env_file=".env", extra="ignore")


settings = Settings()
