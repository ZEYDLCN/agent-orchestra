import logging

from orchestrator.config import settings
from orchestrator.llm.base import LLMProvider
from orchestrator.llm.mock_provider import MockProvider
from orchestrator.llm.resilient import ResilientLLM

logger = logging.getLogger(__name__)

_cached: LLMProvider | None = None


class LLMConfigurationError(RuntimeError):
    pass


def _build_provider(provider_name: str | None = None, model: str | None = None) -> LLMProvider:
    provider = (provider_name or settings.llm_provider).lower()

    if provider == "mock":
        return MockProvider()

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            return _missing_key("anthropic", "ORCH_ANTHROPIC_API_KEY")
        from orchestrator.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings.anthropic_api_key, model or settings.anthropic_model)

    if provider == "openai":
        if not settings.openai_api_key:
            return _missing_key("openai", "ORCH_OPENAI_API_KEY")
        from orchestrator.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings.openai_api_key, model or settings.openai_model)

    if provider == "ollama":
        from orchestrator.llm.ollama_provider import OllamaProvider, is_ollama_available

        if settings.environment == "production" and not is_ollama_available(settings.ollama_base_url):
            raise LLMConfigurationError(
                f"ollama provider misconfigured: {settings.ollama_base_url} is not reachable"
            )
        # development: no reachability check at startup -- an unreachable
        # Ollama surfaces the same way any other LLM failure does, via the
        # ResilientLLM timeout/retry wrapper and analysis_status=failed,
        # not a hard crash on boot
        return OllamaProvider(settings.ollama_base_url, model or settings.ollama_model)

    return _missing_key(provider, None, unknown=True)


def _missing_key(provider: str, env_var: str | None, unknown: bool = False) -> LLMProvider:
    """Development: fall back to the key-free mock so local demos keep
    working. Production: fail fast instead of silently degrading to mock
    output that looks real but isn't -- that's the misleading failure mode
    the fallback was flagged for."""
    reason = f"unknown ORCH_LLM_PROVIDER={provider!r}" if unknown else f"{env_var} is not set"
    if settings.environment == "production":
        raise LLMConfigurationError(f"LLM provider {provider!r} misconfigured: {reason}")
    logger.warning("LLM provider %r misconfigured (%s); falling back to mock", provider, reason)
    return MockProvider()


def get_llm_provider() -> LLMProvider:
    """Picks a provider from ORCH_LLM_PROVIDER, wrapped with a timeout +
    retry budget so a hung/failing call can't hang a worker indefinitely."""
    global _cached
    if _cached is not None:
        return _cached

    inner = _build_provider()
    _cached = ResilientLLM(inner, settings.llm_timeout_seconds, settings.llm_max_attempts)
    return _cached


def build_role_provider(provider_name: str | None = None, model: str | None = None) -> LLMProvider:
    """Build an uncached provider for a control-plane role agent.

    Worker processes can keep using the cached default. Planner and reviewer
    are explicit role instances so their provider/model is visible and can
    be configured independently from trader workers.
    """
    inner = _build_provider(provider_name, model)
    return ResilientLLM(inner, settings.llm_timeout_seconds, settings.llm_max_attempts)


def reset_cache() -> None:
    global _cached
    _cached = None
