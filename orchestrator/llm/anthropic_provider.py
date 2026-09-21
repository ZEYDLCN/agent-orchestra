from orchestrator.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-5"):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def generate(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )
