"""Redis-backed task queue with lease-based durability.

Layout in Redis:
  list       queue:pending:{capability}     -> task_ids waiting to be picked up, per capability
  list       queue:processing                -> task_ids currently leased to a worker (any capability)
  list       queue:dead                       -> task_ids that exhausted retries (dead-letter)
  hash       task:{task_id}                   -> Task JSON (mutable state), TTL'd
  set        job:{job_id}:tasks               -> task_ids belonging to a job
  string     job:{job_id}:meta                -> JobMeta JSON, TTL'd
  sorted set jobs:index                        -> job_id scored by submission time, for job history

Pop is a BLMOVE from the pending list straight into the processing list, so
a task is never "in the ether" between being claimed and being trackable --
if the worker that claimed it dies before ack/nack, the task simply sits in
queue:processing with an expired lease until the reaper (see reap_expired)
reclaims it. This gives at-least-once delivery without needing a separate
"who is still alive" check for every task.

The state transitions that actually mutate a task (claim/ack/nack/cancel)
run as Lua scripts (queue_scripts.py) instead of a plain Python
GET-modify-SET, and every claim carries a fresh lease_token: if the reaper
reclaims a task and hands it to someone else, the original (slow, not
actually dead) worker's eventual ack()/nack() is a stale token and is
ignored server-side instead of clobbering whatever the new claim wrote.
"""
import json
import time
import uuid

from orchestrator.config import settings
from orchestrator.metrics import RedisMetrics
from orchestrator.models import DEFAULT_MAX_ATTEMPTS, Task, TaskStatus
from orchestrator.queue_scripts import (
    ACK_TASK,
    CLAIM_TASK,
    NACK_TASK,
    RECOVER_UNCLAIMED,
    RELEASE_LEASE,
    RENEW_LEASE,
    REQUEST_CANCEL,
)
from orchestrator.redis_client import make_redis_client

PROCESSING_KEY = "queue:processing"
DEAD_KEY = "queue:dead"
JOBS_INDEX_KEY = "jobs:index"
PENDING_KEY_PREFIX = "queue:pending:"


def _pending_key(capability: str) -> str:
    return f"{PENDING_KEY_PREFIX}{capability}"


def _task_key(task_id: str) -> str:
    return f"task:{task_id}"


def _job_tasks_key(job_id: str) -> str:
    return f"job:{job_id}:tasks"


def _job_meta_key(job_id: str) -> str:
    return f"job:{job_id}:meta"


class TaskQueue:
    def __init__(self, redis_url: str | None = None):
        self.client = make_redis_client(redis_url)
        self.metrics = RedisMetrics(redis_url)
        self._register_scripts()

    def _register_scripts(self) -> None:
        # redis-py binds a Script to whichever client registered it
        # (Script.registered_client); rebind_client() re-registers them so
        # swapping self.client (tests do this to point at a fake Redis)
        # doesn't leave these silently talking to the old connection.
        self._claim_script = self.client.register_script(CLAIM_TASK)
        self._ack_script = self.client.register_script(ACK_TASK)
        self._nack_script = self.client.register_script(NACK_TASK)
        self._release_script = self.client.register_script(RELEASE_LEASE)
        self._recover_unclaimed_script = self.client.register_script(RECOVER_UNCLAIMED)
        self._cancel_script = self.client.register_script(REQUEST_CANCEL)
        self._renew_script = self.client.register_script(RENEW_LEASE)

    def rebind_client(self, client) -> None:
        """Point this queue (and its Lua scripts) at a different Redis
        connection. Production has no reason to call this; it exists so
        tests can swap in a fake Redis after construction without the
        scripts staying bound to whatever client __init__ originally saw."""
        self.client = client
        self.metrics.client = client
        self._register_scripts()

    def ping(self) -> bool:
        return bool(self.client.ping())

    # -- job bookkeeping -----------------------------------------------

    def register_job(self, job_id: str, meta: dict | None = None) -> None:
        self.client.zadd(JOBS_INDEX_KEY, {job_id: time.time()})
        self.client.zremrangebyrank(JOBS_INDEX_KEY, 0, -settings.job_index_max_entries - 1)
        if meta is not None:
            self.client.set(_job_meta_key(job_id), json.dumps(meta), ex=settings.task_ttl_seconds)

    def get_job_meta(self, job_id: str) -> dict | None:
        raw = self.client.get(_job_meta_key(job_id))
        return json.loads(raw) if raw else None

    def list_recent_jobs(self, limit: int = 20) -> list[tuple[str, float]]:
        return self.client.zrevrange(JOBS_INDEX_KEY, 0, limit - 1, withscores=True)

    def job_exists(self, job_id: str) -> bool:
        return self.client.zscore(JOBS_INDEX_KEY, job_id) is not None

    def delete_job(self, job_id: str) -> int:
        """Archive/delete a job and everything belonging to it. Returns the
        number of tasks removed. Tasks still pending/running are cancelled
        first so a worker doesn't ack/nack a task whose bookkeeping keys
        just vanished out from under it."""
        task_ids = list(self.client.smembers(_job_tasks_key(job_id)))
        for task_id in task_ids:
            task = self.get_task(task_id)
            if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                self.request_cancel(task_id)
        if task_ids:
            self.client.delete(*(_task_key(tid) for tid in task_ids))
        self.client.delete(_job_tasks_key(job_id))
        self.client.delete(_job_meta_key(job_id))
        self.client.zrem(JOBS_INDEX_KEY, job_id)
        return len(task_ids)

    # -- task lifecycle ---------------------------------------------------

    def push_task(self, task: Task) -> None:
        self.client.set(_task_key(task.task_id), task.model_dump_json(), ex=settings.task_ttl_seconds)
        self.client.sadd(_job_tasks_key(task.job_id), task.task_id)
        self.client.expire(_job_tasks_key(task.job_id), settings.task_ttl_seconds)
        self.client.rpush(_pending_key(task.required_capability), task.task_id)
        self.metrics.incr_submitted(task.required_capability)

    def pop_task(self, capabilities: list[str], worker_id: str, timeout: int = 2) -> Task | None:
        """Atomically move one task_id from a pending queue this worker can
        serve into the processing list, then atomically claim it
        (attempt_count++, fresh lease + lease_token). None if nothing was
        available within `timeout`."""
        task_id = None
        for capability in capabilities:
            task_id = self.client.blmove(
                _pending_key(capability), PROCESSING_KEY, timeout=0.2, src="LEFT", dest="RIGHT"
            )
            if task_id:
                break
        if task_id is None:
            # nothing immediately available on any capability queue; do one
            # real blocking wait on the first capability to avoid busy-polling
            if not capabilities:
                return None
            task_id = self.client.blmove(
                _pending_key(capabilities[0]), PROCESSING_KEY, timeout=timeout, src="LEFT", dest="RIGHT"
            )
        if task_id is None:
            return None

        task_id = str(task_id)
        now = time.time()
        lease_token = uuid.uuid4().hex
        encoded = self._claim_script(
            keys=[_task_key(task_id), PROCESSING_KEY],
            args=[
                worker_id, now, settings.task_lease_seconds, lease_token,
                settings.task_ttl_seconds, task_id,
            ],
        )
        if not encoded:
            # task expired/was deleted out from under us; drop the orphaned id
            self.client.lrem(PROCESSING_KEY, 1, task_id)
            return None
        return Task.model_validate(json.loads(encoded))

    def get_task(self, task_id: str) -> Task | None:
        raw = self.client.get(_task_key(task_id))
        if raw is None:
            return None
        return Task.model_validate(json.loads(raw))

    def save_task(self, task: Task) -> None:
        self.client.set(_task_key(task.task_id), task.model_dump_json(), ex=settings.task_ttl_seconds)

    def ack(self, task_id: str, result: dict, lease_token: str | None = None) -> bool:
        """Task finished successfully: remove its lease and mark done.
        If lease_token is given and no longer matches (the reaper already
        reclaimed this task), the ack is dropped rather than clobbering
        whatever the new claim has since written."""
        ok = self._ack_script(
            keys=[_task_key(task_id), PROCESSING_KEY],
            args=[task_id, json.dumps(result), time.time(), settings.task_ttl_seconds, lease_token or ""],
        )
        if not ok:
            return False
        self.metrics.incr_completed("done")
        duration_ms = result.get("duration_ms")
        if isinstance(duration_ms, (int, float)):
            self.metrics.observe_duration(duration_ms / 1000)
        return True

    def nack(
        self,
        task_id: str,
        error: str,
        lease_token: str | None = None,
        *,
        require_expired: bool = False,
    ) -> bool:
        """Task failed: retry if attempts remain, else dead-letter it.
        Same stale-token guard as ack() when lease_token is given. Reaper
        calls also require the task to still be running with an expired
        lease, checked inside the same Lua transition."""
        raw = self._nack_script(
            keys=[_task_key(task_id), PROCESSING_KEY, DEAD_KEY],
            args=[
                task_id, error, time.time(), settings.task_ttl_seconds,
                lease_token or "", PENDING_KEY_PREFIX, "1" if require_expired else "0",
            ],
        )
        outcome = json.loads(raw)
        if not outcome.get("ok"):
            return False
        if outcome["status"] in ("failed", "cancelled"):
            self.metrics.incr_completed(outcome["status"])
        return True

    def renew_lease(self, task_id: str, lease_token: str) -> bool:
        """Extend a task's lease while it's still legitimately being worked
        on (a slow LLM call, a slow sandbox run) -- without this, a task
        that happens to run longer than task_lease_seconds would get
        reclaimed by the reaper and re-executed by someone else even
        though the original worker is still alive and making progress."""
        return bool(
            self._renew_script(
                keys=[_task_key(task_id)],
                args=[lease_token, time.time(), settings.task_lease_seconds, settings.task_ttl_seconds],
            )
        )

    def release_lease(self, task_id: str, lease_token: str) -> bool:
        """Cooperative release when a worker is shutting down gracefully:
        put the task straight back for someone else to pick up, without
        counting it as a failed attempt."""
        task = self.get_task(task_id)
        if task is None:
            self.client.lrem(PROCESSING_KEY, 1, task_id)
            return False
        return bool(
            self._release_script(
                keys=[
                    _task_key(task_id),
                    _pending_key(task.required_capability),
                    PROCESSING_KEY,
                ],
                args=[task_id, time.time(), settings.task_ttl_seconds, lease_token],
            )
        )

    def reap_expired(self) -> list[str]:
        """Scan the processing list for tasks whose lease expired (worker
        crashed, was force-killed, or the machine died) and requeue or
        dead-letter them. Returns the task_ids that were reclaimed."""
        reclaimed = []
        for task_id in self.client.lrange(PROCESSING_KEY, 0, -1):
            task = self.get_task(task_id)
            if task is None:
                self.client.lrem(PROCESSING_KEY, 1, task_id)
                continue
            if task.status == TaskStatus.PENDING and task.lease_expires_at is None:
                recovered = self._recover_unclaimed_script(
                    keys=[
                        _task_key(task_id),
                        PROCESSING_KEY,
                        _pending_key(task.required_capability),
                    ],
                    args=[task_id],
                )
                if recovered:
                    reclaimed.append(task_id)
                continue
            now = time.time()
            if task.lease_expires_at is None or task.lease_expires_at > now:
                continue
            if self.nack(
                task_id,
                error="lease expired (worker crashed or stopped responding)",
                lease_token=task.lease_token,
                require_expired=True,
            ):
                reclaimed.append(task_id)
        return reclaimed

    def request_cancel(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False
        raw = self._cancel_script(
            keys=[_task_key(task_id), _pending_key(task.required_capability)],
            args=[task_id, time.time(), settings.task_ttl_seconds],
        )
        outcome = json.loads(raw)
        if outcome.get("ok") and outcome.get("was_pending"):
            self.metrics.incr_completed("cancelled")
        return bool(outcome.get("ok"))

    def retry_task(self, task_id: str) -> bool:
        """Manually re-queue a failed/dead/cancelled task with a fresh
        attempt budget."""
        task = self.get_task(task_id)
        if task is None or task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False
        task.status = TaskStatus.PENDING
        task.attempt_count = 0
        task.max_attempts = task.max_attempts or DEFAULT_MAX_ATTEMPTS
        task.cancel_requested = False
        task.error = None
        task.lease_expires_at = None
        task.lease_token = None
        task.assigned_worker = None
        task.updated_at = time.time()
        self.save_task(task)
        self.client.rpush(_pending_key(task.required_capability), task_id)
        return True

    def get_job_tasks(self, job_id: str) -> list[Task]:
        task_ids = list(self.client.smembers(_job_tasks_key(job_id)))
        if not task_ids:
            return []
        raw_values = self.client.mget(_task_key(tid) for tid in task_ids)
        return [Task.model_validate(json.loads(raw)) for raw in raw_values if raw is not None]

    def get_job_summary(self, job_id: str, use_cache: bool = True) -> dict | None:
        """Counts only, not full task bodies -- what the job-history list
        needs. A short cache means N dashboard tabs polling every few
        seconds share one computation instead of each re-reading every
        task in every job on every poll."""
        cache_key = f"{_job_tasks_key(job_id)}:summary_cache"
        if use_cache:
            cached = self.client.get(cache_key)
            if cached is not None:
                return json.loads(cached)

        tasks = self.get_job_tasks(job_id)
        if not tasks:
            return None
        done = sum(1 for t in tasks if t.status == TaskStatus.DONE)
        failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
        cancelled = sum(1 for t in tasks if t.status == TaskStatus.CANCELLED)
        summary = {
            "total": len(tasks),
            "done": done,
            "failed": failed,
            "cancelled": cancelled,
            "pending_or_running": len(tasks) - done - failed - cancelled,
        }
        self.client.set(cache_key, json.dumps(summary), ex=2)
        return summary
