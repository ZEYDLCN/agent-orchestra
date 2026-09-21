from orchestrator.config import settings
from orchestrator.llm import factory
from orchestrator.llm.mock_provider import MockProvider


def test_defaults_to_mock_provider(monkeypatch):
    factory.reset_cache()
    monkeypatch.setattr(settings, "llm_provider", "mock")

    provider = factory.get_llm_provider()

    assert isinstance(provider, MockProvider)
    factory.reset_cache()


def test_falls_back_to_mock_when_anthropic_key_missing(monkeypatch):
    factory.reset_cache()
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    provider = factory.get_llm_provider()

    assert isinstance(provider, MockProvider)
    factory.reset_cache()


def test_caches_provider_instance(monkeypatch):
    factory.reset_cache()
    monkeypatch.setattr(settings, "llm_provider", "mock")

    first = factory.get_llm_provider()
    second = factory.get_llm_provider()

    assert first is second
    factory.reset_cache()
