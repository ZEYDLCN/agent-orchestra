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

    # multi-LLM: "mock" (default, no key needed), "anthropic", "openai", or
    # "ollama" (local model, e.g. Qwen -- no key, no external network call)
    llm_provider: str = "mock"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    llm_timeout_seconds: float = 20.0
    llm_max_attempts: int = 2
    # Optional per-million-token prices: "provider:model|input|output;...".
    # Example: "openai:gpt-example|0.15|0.60". Kept configurable because
    # hosted model prices change; local/mock providers are recorded as $0.
    llm_pricing_raw: str = ""

    # Planner/reviewer control-plane agents. Unset values inherit the
    # primary provider/model above; local CPU inference usually needs a
    # larger llm_timeout_seconds than hosted providers.
    control_llm_provider: str | None = None
    control_llm_model: str | None = None
    agent_run_timeout_seconds: int = 15 * 60
    agent_run_poll_interval_seconds: float = 0.5

    # Heterogeneous agent pool: "name|provider|model" entries separated by
    # ";", e.g. "qwen|ollama|qwen2.5:3b;claude|anthropic|claude-sonnet-5".
    # When set, POST /workers/scale cycles new workers across these
    # profiles instead of every worker sharing the single llm_provider
    # above -- each spawned worker process gets its own ORCH_LLM_PROVIDER
    # (and matching model var) as an environment override, so it's a real
    # separate provider instance per worker, not a shared/simulated one.
    # Unset (default): every worker uses the single provider above,
    # unchanged from before this existed.
    agent_profiles_raw: str = ""

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
