"""Redis-backed task queue + job/result bookkeeping.

Layout in Redis:
  list       queue:pending                 -> task_ids waiting to be picked up
  hash       task:{task_id}                -> Task JSON (mutable state)
  set        job:{job_id}:tasks            -> task_ids belonging to a job
  sorted set jobs:index                    -> job_id scored by submission time, for job history
"""
import json
import time
from typing import Optional

import redis

from orchestrator.models import Task, TaskStatus

QUEUE_KEY = "queue:pending"
JOBS_INDEX_KEY = "jobs:index"
JOBS_INDEX_MAX = 200


def _task_key(task_id: str) -> str:
    return f"task:{task_id}"


def _job_tasks_key(job_id: str) -> str:
    return f"job:{job_id}:tasks"


class TaskQueue:
    def __init__(self, redis_url: str):
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        return bool(self.client.ping())

    def push_task(self, task: Task) -> None:
        self.client.set(_task_key(task.task_id), task.model_dump_json())
        self.client.sadd(_job_tasks_key(task.job_id), task.task_id)
        self.client.rpush(QUEUE_KEY, task.task_id)

    def pop_task(self, timeout: int = 2) -> Optional[Task]:
        item = self.client.blpop([QUEUE_KEY], timeout=timeout)
        if item is None:
            return None
        _, task_id = item
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Optional[Task]:
        raw = self.client.get(_task_key(task_id))
        if raw is None:
            return None
        return Task.model_validate(json.loads(raw))

    def save_task(self, task: Task) -> None:
        self.client.set(_task_key(task.task_id), task.model_dump_json())

    def mark_running(self, task_id: str, worker_id: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        task.status = TaskStatus.RUNNING
        task.assigned_worker = worker_id
        task.updated_at = time.time()
        self.save_task(task)

    def mark_done(self, task_id: str, result: dict) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        task.status = TaskStatus.DONE
        task.result = result
        task.updated_at = time.time()
        self.save_task(task)

    def mark_failed(self, task_id: str, error: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        task.status = TaskStatus.FAILED
        task.error = error
        task.updated_at = time.time()
        self.save_task(task)

    def get_job_tasks(self, job_id: str) -> list[Task]:
        task_ids = self.client.smembers(_job_tasks_key(job_id))
        tasks = [self.get_task(tid) for tid in task_ids]
        return [t for t in tasks if t is not None]

    def register_job(self, job_id: str) -> None:
        self.client.zadd(JOBS_INDEX_KEY, {job_id: time.time()})
        self.client.zremrangebyrank(JOBS_INDEX_KEY, 0, -JOBS_INDEX_MAX - 1)

    def list_recent_jobs(self, limit: int = 20) -> list[tuple[str, float]]:
        return self.client.zrevrange(JOBS_INDEX_KEY, 0, limit - 1, withscores=True)
