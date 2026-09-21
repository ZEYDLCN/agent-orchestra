import hashlib

from orchestrator.llm.base import LLMProvider


class MockProvider(LLMProvider):
    """Deterministic, API-key-free stand-in so the pipeline is runnable and
    testable without live LLM credentials. Used automatically when no
    provider API key is configured."""

    name = "mock"

    def generate(self, prompt: str) -> str:
        digest = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        return f"[mock-llm:{digest}] {prompt[:400]}"
