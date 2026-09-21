import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    job_id: str
    payload: dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    assigned_worker: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class JobSubmitRequest(BaseModel):
    tasks: list[dict[str, Any]]


class JobSubmitResponse(BaseModel):
    job_id: str
    task_ids: list[str]


class WorkerInfo(BaseModel):
    worker_id: str
    pid: int
    worktree_path: str
    branch: str
    status: str = "running"


class ScaleRequest(BaseModel):
    count: int
