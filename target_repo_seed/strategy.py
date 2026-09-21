"""Sample trading strategy used as the Day-1 orchestration demo payload.

Each worker checks this file out in its own isolated git worktree and
backtests one parameter combination. The scoring function is a deterministic
stand-in for a real backtest so demo runs are reproducible without market
data.
"""
import hashlib


def backtest(params: dict) -> float:
    fast_ma = params.get("fast_ma", 10)
    slow_ma = params.get("slow_ma", 50)

    key = f"{fast_ma}:{slow_ma}".encode()
    noise = int(hashlib.sha256(key).hexdigest(), 16) % 1000 / 1000

    spread = slow_ma - fast_ma
    base_score = max(0.0, spread) / (fast_ma + slow_ma)
    return round(base_score * 100 + noise, 4)
