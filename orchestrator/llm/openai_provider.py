from orchestrator.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    def generate(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
