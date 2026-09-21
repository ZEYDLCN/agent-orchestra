"""Wraps any LLMProvider with a timeout and a small retry budget, so a
hung or rate-limited API call can't hang a worker forever -- previously
there was no timeout at all, and a stuck call meant a stuck worker holding
its task lease until it expired."""
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from orchestrator.llm.base import LLMProvider


class LLMTimeoutError(RuntimeError):
    pass


class ResilientLLM(LLMProvider):
    def __init__(
        self,
        inner: LLMProvider,
        timeout_seconds: float,
        max_attempts: int,
        backoff_base_seconds: float = 2.0,
    ):
        self._inner = inner
        self._timeout = timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base_seconds
        self.name = inner.name

    def generate(self, prompt: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            # not a context manager on purpose: __exit__ would block until
            # the submitted thread finishes, which defeats the timeout if
            # the underlying call is truly hung. A timed-out thread is left
            # to finish (or hang) in the background instead.
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(self._inner.generate, prompt)
                return future.result(timeout=self._timeout)
            except FutureTimeoutError:
                last_exc = LLMTimeoutError(f"LLM call timed out after {self._timeout}s")
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
            finally:
                pool.shutdown(wait=False)
            if attempt < self._max_attempts:
                time.sleep(min(self._backoff_base**attempt, 5))
        assert last_exc is not None
        raise last_exc
