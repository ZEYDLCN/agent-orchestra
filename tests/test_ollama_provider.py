import requests

from orchestrator.llm.ollama_provider import OllamaProvider, is_ollama_available


class FakeResponse:
    def __init__(self, json_data=None, ok=True, status_code=200):
        self._json_data = json_data or {}
        self.ok = ok
        self.status_code = status_code

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def test_generate_posts_to_ollama_api_and_returns_response_text(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse({"response": "merhaba dunya"})

    monkeypatch.setattr(requests, "post", fake_post)

    provider = OllamaProvider("http://localhost:11434", "qwen2.5:3b")
    result = provider.generate("test prompt")

    assert result == "merhaba dunya"
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["json"] == {"model": "qwen2.5:3b", "prompt": "test prompt", "stream": False}


def test_name_includes_model_for_heterogeneous_visibility():
    provider = OllamaProvider("http://localhost:11434", "qwen2.5:3b")
    assert provider.name == "ollama:qwen2.5:3b"


def test_trailing_slash_in_base_url_is_normalized(monkeypatch):
    captured = {}

    def fake_post(url, **kw):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    OllamaProvider("http://localhost:11434/", "qwen2.5:3b").generate("hi")
    assert captured["url"] == "http://localhost:11434/api/generate"


def test_generate_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **kw: FakeResponse(ok=False, status_code=500))
    provider = OllamaProvider("http://localhost:11434", "qwen2.5:3b")
    try:
        provider.generate("hi")
        raise AssertionError("expected HTTPError")
    except requests.HTTPError:
        pass


def test_generate_propagates_connection_error_for_resilient_wrapper_to_catch(monkeypatch):
    def raise_connection_error(*a, **kw):
        raise requests.ConnectionError("ollama not running")

    monkeypatch.setattr(requests, "post", raise_connection_error)
    provider = OllamaProvider("http://localhost:11434", "qwen2.5:3b")
    try:
        provider.generate("hi")
        raise AssertionError("expected ConnectionError")
    except requests.ConnectionError:
        pass


def test_is_ollama_available_true_when_reachable(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse(ok=True))
    assert is_ollama_available("http://localhost:11434") is True


def test_is_ollama_available_false_when_unreachable(monkeypatch):
    def raise_connection_error(*a, **kw):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", raise_connection_error)
    assert is_ollama_available("http://localhost:11434") is False


def test_is_ollama_available_false_on_non_ok_status(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse(ok=False, status_code=503))
    assert is_ollama_available("http://localhost:11434") is False
