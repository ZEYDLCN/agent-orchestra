"""Redis-backed worker directory, replacing the old in-process Python dict.

Why: a dict living inside one FastAPI process means worker visibility dies
with that process, remote workers on other machines never show up, and two
uvicorn processes each see a different worker list. Storing worker state in
Redis with a TTL fixes all three: any orchestrator process (or a plain
`redis-cli`) can list the fleet, a worker on another machine is visible the
moment it heartbeats, and a worker that crashes simply ages out of the TTL
without needing anyone to notice and clean up after it.

Stopping a worker is cooperative, not signal-based: DELETE /workers/{id}
sets a stop flag in Redis; the worker polls that flag itself on every loop
iteration and exits gracefully (releasing any task it's holding). This also
sidesteps Windows' limited POSIX-signal support -- Popen.terminate() there
is a hard kill with no graceful hook, so a cooperative flag is the only
portable way to ask a worker to wind down without losing in-flight work.
"""
import time

from orchestrator.config import settings
from orchestrator.models import WorkerInfo
from orchestrator.redis_client import make_redis_client

WORKER_KEY_PREFIX = "worker:"
WORKER_KEY_SUFFIX = ":info"
STOP_KEY_SUFFIX = ":stop"


def _info_key(worker_id: str) -> str:
    return f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_KEY_SUFFIX}"


def _stop_key(worker_id: str) -> str:
    return f"{WORKER_KEY_PREFIX}{worker_id}{STOP_KEY_SUFFIX}"


class WorkerRegistry:
    def __init__(self, redis_url: str | None = None):
        self.client = make_redis_client(redis_url)

    def heartbeat(self, info: WorkerInfo) -> None:
        info.last_heartbeat = time.time()
        info.status = "running"
        self.client.set(_info_key(info.worker_id), info.model_dump_json(), ex=settings.worker_ttl_seconds)

    def deregister(self, worker_id: str) -> None:
        self.client.delete(_info_key(worker_id))
        self.client.delete(_stop_key(worker_id))

    def get(self, worker_id: str) -> WorkerInfo | None:
        raw = self.client.get(_info_key(worker_id))
        return WorkerInfo.model_validate_json(raw) if raw else None

    def list_all(self) -> list[WorkerInfo]:
        workers = []
        cursor = 0
        pattern = f"{WORKER_KEY_PREFIX}*{WORKER_KEY_SUFFIX}"
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=pattern, count=100)
            for key in keys:
                raw = self.client.get(key)
                if raw:
                    workers.append(WorkerInfo.model_validate_json(raw))
            if cursor == 0:
                break
        return workers

    def request_stop(self, worker_id: str) -> None:
        self.client.set(_stop_key(worker_id), "1", ex=settings.worker_ttl_seconds * 4)

    def is_stop_requested(self, worker_id: str) -> bool:
        return bool(self.client.exists(_stop_key(worker_id)))
