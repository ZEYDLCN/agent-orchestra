"""Fixed-window rate limiting, backed by Redis so it holds even if the
orchestrator runs as more than one process. A missing per-request limit
was one of the review's flagged gaps -- "herkes worker durdurabilir" etc.
This bounds request *volume*; it's not a substitute for the auth in
auth.py, which bounds *who*."""
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from orchestrator.config import settings
from orchestrator.redis_client import make_redis_client

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = make_redis_client()
    return _client


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if settings.rate_limit_per_minute <= 0:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        window = int(time.time() // 60)
        key = f"ratelimit:{client_ip}:{window}"

        try:
            redis_client = _get_client()
            count = redis_client.incr(key)
            if count == 1:
                redis_client.expire(key, 60)
        except Exception:  # noqa: BLE001
            # Redis hiccup shouldn't take the whole API down over a
            # best-effort rate limit
            return await call_next(request)

        if count > settings.rate_limit_per_minute:
            return JSONResponse(
                {"detail": "rate limit exceeded, try again shortly"}, status_code=429
            )
        return await call_next(request)
