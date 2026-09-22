from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


def estimate_tokens(text: str) -> int:
    """Dependency-free fallback for providers that do not return usage.

    The value is deliberately marked as estimated everywhere it is exposed;
    provider-reported token counters always take precedence.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.total_tokens:
            self.total_tokens = self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LLMResponse:
    text: str
    usage: TokenUsage


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Return a short text completion for the given prompt."""

    def generate_with_usage(self, prompt: str) -> LLMResponse:
        """Return text plus token counters, estimating only as a fallback."""
        text = self.generate(prompt)
        return LLMResponse(
            text=text,
            usage=TokenUsage(
                input_tokens=estimate_tokens(prompt),
                output_tokens=estimate_tokens(text),
                estimated=True,
            ),
        )
