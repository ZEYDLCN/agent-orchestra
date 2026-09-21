"""Business-level metrics, backed by Redis counters rather than in-process
prometheus_client objects.

Why: workers are separate OS processes by design (that's what makes them
independently crashable/killable/deployable to other machines), so a
Counter incremented inside a worker process lives in *that* process's
memory -- the orchestrator process serving /metrics would never see it.
Redis is the one thing every process here already shares, so counters live
there instead, and a Collector reads them fresh on every scrape (see
WorkerFleetCollector in main.py for the same pattern applied to worker
counts).
"""
from orchestrator.redis_client import make_redis_client

_METRIC_PREFIX = "metrics:"


class RedisMetrics:
    def __init__(self, redis_url: str | None = None):
        self.client = make_redis_client(redis_url)

    def incr_submitted(self, capability: str) -> None:
        self.client.incr(f"{_METRIC_PREFIX}tasks_submitted:{capability}")

    def incr_completed(self, status: str) -> None:
        self.client.incr(f"{_METRIC_PREFIX}tasks_completed:{status}")

    def incr_reclaimed(self, count: int = 1) -> None:
        self.client.incrby(f"{_METRIC_PREFIX}tasks_reclaimed", count)

    def observe_duration(self, seconds: float) -> None:
        pipe = self.client.pipeline()
        pipe.incr(f"{_METRIC_PREFIX}task_duration_count")
        pipe.incrbyfloat(f"{_METRIC_PREFIX}task_duration_sum", seconds)
        pipe.execute()

    def snapshot(self) -> dict:
        """Everything needed for a /metrics scrape, read in one pass."""
        keys = list(self.client.scan_iter(match=f"{_METRIC_PREFIX}*"))
        if not keys:
            return {"submitted": {}, "completed": {}, "reclaimed": 0, "duration_count": 0, "duration_sum": 0.0}

        values = self.client.mget(keys)
        raw = dict(zip(keys, values, strict=True))

        submitted, completed = {}, {}
        reclaimed = 0
        duration_count, duration_sum = 0, 0.0
        for key, value in raw.items():
            if value is None:
                continue
            if key.startswith(f"{_METRIC_PREFIX}tasks_submitted:"):
                submitted[key.rsplit(":", 1)[1]] = int(value)
            elif key.startswith(f"{_METRIC_PREFIX}tasks_completed:"):
                completed[key.rsplit(":", 1)[1]] = int(value)
            elif key == f"{_METRIC_PREFIX}tasks_reclaimed":
                reclaimed = int(value)
            elif key == f"{_METRIC_PREFIX}task_duration_count":
                duration_count = int(value)
            elif key == f"{_METRIC_PREFIX}task_duration_sum":
                duration_sum = float(value)
        return {
            "submitted": submitted,
            "completed": completed,
            "reclaimed": reclaimed,
            "duration_count": duration_count,
            "duration_sum": duration_sum,
        }
