import time

import pytest

from orchestrator.llm.base import LLMProvider
from orchestrator.llm.resilient import LLMTimeoutError, ResilientLLM


class SlowProvider(LLMProvider):
    name = "slow"

    def generate(self, prompt: str) -> str:
        time.sleep(0.5)
        return "too late"


class FlakyProvider(LLMProvider):
    name = "flaky"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        if self.calls < 2:
            raise RuntimeError("transient failure")
        return "ok on retry"


class AlwaysFailsProvider(LLMProvider):
    name = "broken"

    def generate(self, prompt: str) -> str:
        raise RuntimeError("permanently broken")


def test_raises_timeout_error_instead_of_hanging():
    resilient = ResilientLLM(SlowProvider(), timeout_seconds=0.05, max_attempts=1)
    with pytest.raises(LLMTimeoutError):
        resilient.generate("hi")


def test_retries_transient_failures_and_succeeds():
    flaky = FlakyProvider()
    resilient = ResilientLLM(flaky, timeout_seconds=5, max_attempts=3, backoff_base_seconds=0.01)

    result = resilient.generate("hi")

    assert result == "ok on retry"
    assert flaky.calls == 2


def test_raises_after_exhausting_attempts():
    resilient = ResilientLLM(
        AlwaysFailsProvider(), timeout_seconds=5, max_attempts=2, backoff_base_seconds=0.01
    )
    with pytest.raises(RuntimeError, match="permanently broken"):
        resilient.generate("hi")


def test_name_passthrough():
    resilient = ResilientLLM(FlakyProvider(), timeout_seconds=5, max_attempts=1)
    assert resilient.name == "flaky"
