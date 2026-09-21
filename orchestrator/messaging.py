"""Inter-agent messaging over Redis pub/sub. Workers broadcast findings
(e.g. task results, commentary) on a shared channel per job so peer workers
and the orchestrator can observe progress live.

Tasks here typically finish in single-digit milliseconds, so a subscriber
that only used plain pub/sub would race the workers and miss most events
(subscribe happens after they already fired). Every publish is therefore
also appended to a short-lived per-job log; subscribe() replays that log
before switching to live pub/sub, so late subscribers (a dashboard opened
after a job was submitted, a client that reconnects) still see everything.
The replay-then-listen handoff has a narrow window where an event can be
delivered twice -- acceptable for this use case, callers can dedupe by
(worker_id, params, ts) if that ever matters.
"""
import json
import time
from collections.abc import Iterator
from typing import Any

from orchestrator.redis_client import make_pubsub_redis_client

LOG_MAX_ENTRIES = 200
LOG_TTL_SECONDS = 3600


def _channel(job_id: str) -> str:
    return f"job:{job_id}:events"


def _log_key(job_id: str) -> str:
    return f"job:{job_id}:events:log"


class MessageBus:
    def __init__(self, redis_url: str | None = None):
        self.client = make_pubsub_redis_client(redis_url)

    def publish(self, job_id: str, event_type: str, data: dict[str, Any]) -> None:
        message = {"type": event_type, "data": data, "ts": time.time()}
        payload = json.dumps(message)

        log_key = _log_key(job_id)
        self.client.rpush(log_key, payload)
        self.client.ltrim(log_key, -LOG_MAX_ENTRIES, -1)
        self.client.expire(log_key, LOG_TTL_SECONDS)

        self.client.publish(_channel(job_id), payload)

    def subscribe(self, job_id: str, replay: bool = True) -> Iterator[dict[str, Any]]:
        pubsub = self.client.pubsub()
        pubsub.subscribe(_channel(job_id))
        try:
            if replay:
                for raw in self.client.lrange(_log_key(job_id), 0, -1):
                    yield json.loads(raw)
            for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue
                yield json.loads(raw["data"])
        finally:
            pubsub.close()
