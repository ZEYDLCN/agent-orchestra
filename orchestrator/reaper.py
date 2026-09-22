"""Background sweep that reclaims tasks whose lease expired -- the worker
holding them crashed, was force-killed, or the machine died. Runs inside
the orchestrator process so it starts automatically with `uvicorn
orchestrator.main:app` rather than needing a separate cron/process.

If more than one orchestrator process is running against the same Redis
(multiple uvicorn workers, a multi-replica deployment), each one would
otherwise run its own Reaper independently. reap_expired() itself is
Lua-atomic per task so that wouldn't corrupt anything, but two reapers
racing on the same expired task_id can both call nack() on it -- the
second call sees a task that's already back in 'pending' (not 'running'),
and since nack doesn't check current status, it can double-RPUSH the same
task_id onto the pending queue, letting two workers claim and (wastefully,
not corruptly -- the lease-token fencing still protects the actual data)
run it at once. A simple leader lease -- only the current leader's tick
actually reaps -- avoids that without needing distributed consensus:
losing leadership just means this process skips a tick, and the tasks it
would have reaped stay expired for the next tick, whoever wins it.
"""
import logging
import threading
import uuid

from orchestrator.config import settings
from orchestrator.queue import TaskQueue

logger = logging.getLogger(__name__)

LEADER_KEY = "lock:reaper_leader"

# KEYS[1] = leader key, ARGV[1] = our instance id, ARGV[2] = ttl_seconds
_RENEW_LEADERSHIP_IF_OWNER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
else
  return 0
end
"""

# KEYS[1] = leader key, ARGV[1] = our instance id
_RELEASE_LEADERSHIP_IF_OWNER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
else
  return 0
end
"""


class Reaper:
    def __init__(self, task_queue: TaskQueue):
        self._queue = task_queue
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._instance_id = uuid.uuid4().hex
        self._renew_leadership_script = self._queue.client.register_script(_RENEW_LEADERSHIP_IF_OWNER)
        self._release_leadership_script = self._queue.client.register_script(_RELEASE_LEADERSHIP_IF_OWNER)
        self._is_leader = False

    def _leader_ttl(self) -> int:
        return max(settings.reaper_interval_seconds * 3, 10)

    def _try_become_or_stay_leader(self) -> bool:
        if not self._is_leader:
            acquired = self._queue.client.set(
                LEADER_KEY, self._instance_id, nx=True, ex=self._leader_ttl()
            )
            if acquired:
                logger.info("became reaper leader (instance %s)", self._instance_id)
                self._is_leader = True
            return self._is_leader

        renewed = bool(
            self._renew_leadership_script(keys=[LEADER_KEY], args=[self._instance_id, self._leader_ttl()])
        )
        if not renewed:
            logger.warning("lost reaper leadership (instance %s)", self._instance_id)
            self._is_leader = False
        return self._is_leader

    def _loop(self) -> None:
        while not self._stop_event.wait(settings.reaper_interval_seconds):
            try:
                if not self._try_become_or_stay_leader():
                    continue
                reclaimed = self._queue.reap_expired()
                if reclaimed:
                    self._queue.metrics.incr_reclaimed(len(reclaimed))
                    logger.info("reaper reclaimed %d expired task(s): %s", len(reclaimed), reclaimed)
            except Exception:  # noqa: BLE001
                logger.exception("reaper tick failed")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._is_leader:
            # best-effort: let another process take over immediately on
            # a clean shutdown instead of waiting out the full leader TTL
            self._release_leadership_script(keys=[LEADER_KEY], args=[self._instance_id])
            self._is_leader = False
