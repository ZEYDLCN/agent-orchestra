from orchestrator.config import settings
from orchestrator.llm.base import LLMProvider
from orchestrator.llm.mock_provider import MockProvider

_cached: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Picks a provider from ORCH_LLM_PROVIDER, falling back to the
    key-free mock provider when unset or its API key is missing --
    keeps the whole pipeline runnable without live LLM credentials."""
    global _cached
    if _cached is not None:
        return _cached

    provider = settings.llm_provider.lower()

    if provider == "anthropic" and settings.anthropic_api_key:
        from orchestrator.llm.anthropic_provider import AnthropicProvider

        _cached = AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
    elif provider == "openai" and settings.openai_api_key:
        from orchestrator.llm.openai_provider import OpenAIProvider

        _cached = OpenAIProvider(settings.openai_api_key, settings.openai_model)
    else:
        _cached = MockProvider()

    return _cached


def reset_cache() -> None:
    global _cached
    _cached = None
