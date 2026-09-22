from orchestrator.llm.base import LLMProvider, LLMResponse, TokenUsage


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self._model = model
        self.name = f"openai:{model}"

    def generate(self, prompt: str) -> str:
        return self.generate_with_usage(prompt).text

    def generate_with_usage(self, prompt: str) -> LLMResponse:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = response.usage
        return LLMResponse(
            text=response.choices[0].message.content or "",
            usage=TokenUsage(
                input_tokens=int(usage.prompt_tokens or 0),
                output_tokens=int(usage.completion_tokens or 0),
                total_tokens=int(usage.total_tokens or 0),
            ),
        )
