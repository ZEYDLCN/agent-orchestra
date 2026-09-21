"""Shared memory across agents + a lightweight retrieval step ("RAG-lite").

Task payloads here are structured numeric params (not free text), so instead
of a text-embedding vector store this retrieves by parameter-space distance.
Swapping in a real embedding/vector-DB backend later only touches this file.

Entries are tagged with strategy_id so similarity search doesn't mix runs
from unrelated strategies that happen to share a param name -- e.g. two
different strategies both taking a "period" param with very different
meaning and scale would otherwise be treated as comparable.
"""
import json
from typing import Any

from orchestrator.redis_client import make_redis_client

MEMORY_KEY = "memory:entries"
MAX_ENTRIES = 500


def _params_distance(a: dict, b: dict) -> float:
    keys = set(a) & set(b)
    if not keys:
        return float("inf")
    total = 0.0
    matched = 0
    for k in keys:
        try:
            total += (float(a[k]) - float(b[k])) ** 2
            matched += 1
        except (TypeError, ValueError):
            continue  # non-numeric param on this key; skip rather than crash
    if matched == 0:
        return float("inf")
    return total**0.5


class SharedMemory:
    def __init__(self, redis_url: str | None = None):
        self.client = make_redis_client(redis_url)

    def record(self, entry: dict[str, Any], strategy_id: str = "default") -> None:
        entry = {**entry, "strategy_id": strategy_id}
        self.client.rpush(MEMORY_KEY, json.dumps(entry))
        self.client.ltrim(MEMORY_KEY, -MAX_ENTRIES, -1)

    def all_entries(self) -> list[dict[str, Any]]:
        return [json.loads(e) for e in self.client.lrange(MEMORY_KEY, 0, -1)]

    def retrieve_similar(
        self, params: dict, top_k: int = 3, strategy_id: str = "default"
    ) -> list[dict[str, Any]]:
        candidates = [
            (_params_distance(params, e["params"]), e)
            for e in self.all_entries()
            if e.get("params") and e.get("strategy_id", "default") == strategy_id
        ]
        candidates = [c for c in candidates if c[0] != float("inf")]
        candidates.sort(key=lambda c: c[0])
        return [entry for _, entry in candidates[:top_k]]
