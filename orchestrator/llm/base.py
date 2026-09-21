from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Return a short text completion for the given prompt."""
