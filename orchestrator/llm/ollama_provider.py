"""Local LLM via Ollama (https://ollama.com) -- e.g. Qwen2.5, Llama3, etc.
running on the same machine as the worker, no API key or external network
call required. This is what makes an agent genuinely heterogeneous rather
than just differently-labeled: a worker configured with this provider
does real inference with its own local weights, independent of whatever
hosted provider other workers in the pool are using.

Talks to Ollama's plain HTTP API directly (no extra dependency -- `requests`
is already used elsewhere in this project).
"""
import requests

from orchestrator.llm.base import LLMProvider, LLMResponse, TokenUsage


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self.name = f"ollama:{model}"

    def generate(self, prompt: str) -> str:
        return self.generate_with_usage(prompt).text

    def generate_with_usage(self, prompt: str) -> LLMResponse:
        response = requests.post(
            f"{self._base_url}/api/generate",
            json={"model": self._model, "prompt": prompt, "stream": False},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        input_tokens = int(payload.get("prompt_eval_count") or 0)
        output_tokens = int(payload.get("eval_count") or 0)
        return LLMResponse(
            text=payload.get("response", ""),
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated=False,
                metadata={
                    key: payload[key]
                    for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")
                    if key in payload
                },
            ),
        )


def is_ollama_available(base_url: str, timeout: float = 3.0) -> bool:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        return response.ok
    except requests.RequestException:
        return False
