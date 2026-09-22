import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


DEFAULT_MAX_ATTEMPTS = 3


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    job_id: str
    payload: dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    assigned_worker: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    required_capability: str = "backtest"
    attempt_count: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_expires_at: float | None = None
    lease_token: str | None = None
    cancel_requested: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class JobSubmitRequest(BaseModel):
    tasks: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    required_capability: str = "backtest"
    parent_job_id: str | None = None
    kind: str = "grid_sweep"


class JobSubmitResponse(BaseModel):
    job_id: str
    task_ids: list[str]


class WorkerInfo(BaseModel):
    worker_id: str
    pid: int | None = None
    hostname: str | None = None
    worktree_path: str
    branch: str
    capabilities: list[str] = ["backtest"]
    current_task_id: str | None = None
    version: str | None = None
    repo_revision: str | None = None
    status: str = "running"
    last_heartbeat: float | None = None
    managed_locally: bool = False
    agent_profile: str | None = None
    llm_provider: str | None = None


class ScaleRequest(BaseModel):
    count: int = Field(ge=0, le=50)


class AgentProfile(BaseModel):
    """One entry in a heterogeneous worker pool: a named LLM
    configuration a worker process can be spawned with. See
    ORCH_AGENT_PROFILES_RAW in config.py."""
    name: str
    provider: str
    model: str | None = None


class AgentRunStatus(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    max_rounds: int = Field(default=2, ge=1, le=3)
    max_tasks_per_round: int = Field(default=9, ge=1, le=50)
    trader_count: int = Field(default=3, ge=1, le=10)


class AgentRun(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    goal: str
    status: AgentRunStatus = AgentRunStatus.PLANNING
    stage: str = "planner"
    max_rounds: int = 2
    max_tasks_per_round: int = 9
    trader_count: int = 3
    current_round: int = 0
    current_job_id: str | None = None
    job_ids: list[str] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    final_result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class LLMTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str | None = None
    job_id: str | None = None
    task_id: str | None = None
    round_number: int | None = None
    role: str
    agent_id: str
    provider: str
    model: str | None = None
    status: str = "completed"
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tokens_estimated: bool = False
    duration_ms: float = 0.0
    cost_usd: float | None = None
    error: str | None = None
    started_at: float = Field(default_factory=time.time)
    completed_at: float = Field(default_factory=time.time)


class JobMeta(BaseModel):
    job_id: str
    created_at: float = Field(default_factory=time.time)
    parent_job_id: str | None = None
    kind: str = "grid_sweep"
    required_capability: str = "backtest"
