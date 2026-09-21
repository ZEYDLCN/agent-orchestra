"""Inter-agent messaging over Redis pub/sub. Workers broadcast findings
(e.g. task results, commentary) on a shared channel per job so peer workers
and the orchestrator can observe progress live."""
import json
import time
from typing import Any, Iterator

import redis


def _channel(job_id: str) -> str:
    return f"job:{job_id}:events"


class MessageBus:
    def __init__(self, redis_url: str):
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def publish(self, job_id: str, event_type: str, data: dict[str, Any]) -> None:
        message = {"type": event_type, "data": data, "ts": time.time()}
        self.client.publish(_channel(job_id), json.dumps(message))

    def subscribe(self, job_id: str) -> Iterator[dict[str, Any]]:
        pubsub = self.client.pubsub()
        pubsub.subscribe(_channel(job_id))
        try:
            for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue
                yield json.loads(raw["data"])
        finally:
            pubsub.close()
