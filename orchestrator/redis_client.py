"""Single place that builds Redis client connections, so every consumer
(queue, messaging, memory, worker registry) gets the same connect/socket
timeouts and retry-on-timeout behavior instead of each hand-rolling it."""
import redis

from orchestrator.config import settings


def make_redis_client(url: str | None = None) -> redis.Redis:
    return redis.Redis.from_url(
        url or settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_connect_timeout,
        socket_timeout=settings.redis_socket_timeout,
        retry_on_timeout=True,
        health_check_interval=30,
    )


def make_pubsub_redis_client(url: str | None = None) -> redis.Redis:
    """For clients that hold a long-lived blocking pub/sub subscription
    (MessageBus). A finite socket_timeout would make redis-py raise a
    timeout error whenever no message arrives within that window -- fatal
    for something meant to block indefinitely, like an SSE feed waiting on
    the next event. Only the connect timeout applies here."""
    return redis.Redis.from_url(
        url or settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_connect_timeout,
    )
