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


class ScaleRequest(BaseModel):
    count: int = Field(ge=0, le=50)


class JobMeta(BaseModel):
    job_id: str
    created_at: float = Field(default_factory=time.time)
    parent_job_id: str | None = None
    kind: str = "grid_sweep"
    required_capability: str = "backtest"
