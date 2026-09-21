import pytest

from orchestrator.config import settings
from orchestrator.llm import factory
from orchestrator.llm.factory import LLMConfigurationError
from orchestrator.llm.resilient import ResilientLLM


def test_defaults_to_mock_provider(monkeypatch):
    factory.reset_cache()
    monkeypatch.setattr(settings, "llm_provider", "mock")

    provider = factory.get_llm_provider()

    assert isinstance(provider, ResilientLLM)
    assert provider.name == "mock"
    factory.reset_cache()


def test_falls_back_to_mock_when_anthropic_key_missing_in_development(monkeypatch):
    factory.reset_cache()
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    provider = factory.get_llm_provider()

    assert provider.name == "mock"
    factory.reset_cache()


def test_fails_fast_when_anthropic_key_missing_in_production(monkeypatch):
    factory.reset_cache()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    with pytest.raises(LLMConfigurationError):
        factory.get_llm_provider()
    factory.reset_cache()


def test_caches_provider_instance(monkeypatch):
    factory.reset_cache()
    monkeypatch.setattr(settings, "llm_provider", "mock")

    first = factory.get_llm_provider()
    second = factory.get_llm_provider()

    assert first is second
    factory.reset_cache()
