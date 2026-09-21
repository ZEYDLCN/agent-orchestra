"""Shared memory across agents + a lightweight retrieval step ("RAG-lite").

Task payloads here are structured numeric params (not free text), so instead
of a text-embedding vector store this retrieves by parameter-space distance.
Swapping in a real embedding/vector-DB backend later only touches this file.
"""
import json
from typing import Any

import redis

MEMORY_KEY = "memory:entries"
MAX_ENTRIES = 500


def _params_distance(a: dict, b: dict) -> float:
    keys = set(a) & set(b)
    if not keys:
        return float("inf")
    return sum((float(a[k]) - float(b[k])) ** 2 for k in keys) ** 0.5


class SharedMemory:
    def __init__(self, redis_url: str):
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def record(self, entry: dict[str, Any]) -> None:
        self.client.rpush(MEMORY_KEY, json.dumps(entry))
        self.client.ltrim(MEMORY_KEY, -MAX_ENTRIES, -1)

    def all_entries(self) -> list[dict[str, Any]]:
        return [json.loads(e) for e in self.client.lrange(MEMORY_KEY, 0, -1)]

    def retrieve_similar(self, params: dict, top_k: int = 3) -> list[dict[str, Any]]:
        candidates = [
            (_params_distance(params, e["params"]), e)
            for e in self.all_entries()
            if e.get("params")
        ]
        candidates = [c for c in candidates if c[0] != float("inf")]
        candidates.sort(key=lambda c: c[0])
        return [entry for _, entry in candidates[:top_k]]
