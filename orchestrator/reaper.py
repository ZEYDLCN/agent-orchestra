"""Background sweep that reclaims tasks whose lease expired -- the worker
holding them crashed, was force-killed, or the machine died. Runs inside
the orchestrator process so it starts automatically with `uvicorn
orchestrator.main:app` rather than needing a separate cron/process."""
import logging
import threading

from orchestrator.config import settings
from orchestrator.queue import TaskQueue

logger = logging.getLogger(__name__)


class Reaper:
    def __init__(self, task_queue: TaskQueue):
        self._queue = task_queue
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop_event.wait(settings.reaper_interval_seconds):
            try:
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
