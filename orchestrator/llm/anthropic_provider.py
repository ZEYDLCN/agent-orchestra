from orchestrator.llm.base import LLMProvider, LLMResponse, TokenUsage


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-5"):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self.name = f"anthropic:{model}"

    def generate(self, prompt: str) -> str:
        return self.generate_with_usage(prompt).text

    def generate_with_usage(self, prompt: str) -> LLMResponse:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(
            text="".join(block.text for block in response.content if block.type == "text"),
            usage=TokenUsage(
                input_tokens=int(response.usage.input_tokens or 0),
                output_tokens=int(response.usage.output_tokens or 0),
            ),
        )
