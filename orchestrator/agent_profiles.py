"""Parses ORCH_AGENT_PROFILES_RAW into a list of AgentProfile.

Format: "name|provider|model" entries separated by ";", e.g.
    ORCH_AGENT_PROFILES_RAW="qwen|ollama|qwen2.5:3b;claude|anthropic|claude-sonnet-5;gpt|openai|gpt-4o-mini"

"|" (not ":") separates fields because Ollama model tags themselves
contain ":" (qwen2.5:3b).
"""
from collections import Counter
from collections.abc import Iterable

from orchestrator.models import AgentProfile

SUPPORTED_PROVIDERS = frozenset({"mock", "anthropic", "openai", "ollama"})


class AgentProfileConfigurationError(ValueError):
    """Raised when ORCH_AGENT_PROFILES_RAW cannot describe a usable pool."""


def parse_agent_profiles(raw: str) -> list[AgentProfile]:
    profiles: list[AgentProfile] = []
    names: set[str] = set()
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) not in (2, 3):
            raise AgentProfileConfigurationError(
                f"invalid agent profile {chunk!r}; expected name|provider|model"
            )
        name = parts[0]
        provider = parts[1].lower()
        model = parts[2] if len(parts) > 2 and parts[2] else None
        if not name:
            raise AgentProfileConfigurationError("agent profile name cannot be empty")
        if not provider:
            raise AgentProfileConfigurationError(f"agent profile {name!r} has no provider")
        if provider not in SUPPORTED_PROVIDERS:
            choices = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise AgentProfileConfigurationError(
                f"agent profile {name!r} uses unsupported provider {provider!r}; choose {choices}"
            )
        normalized_name = name.casefold()
        if normalized_name in names:
            raise AgentProfileConfigurationError(f"duplicate agent profile name {name!r}")
        names.add(normalized_name)
        profiles.append(AgentProfile(name=name, provider=provider, model=model))
    return profiles


def select_profiles_for_scale(
    profiles: list[AgentProfile], existing_profile_names: Iterable[str | None], count: int
) -> list[AgentProfile]:
    """Select a balanced sequence for newly spawned workers.

    Looking at the profiles that survived a scale-down keeps later scale-up
    calls heterogeneous. Restarting the round-robin at index zero on every
    request would produce A,A for the common scale 0 -> 1 -> 2 sequence.
    """
    if not profiles or count <= 0:
        return []
    usage = Counter(name for name in existing_profile_names if name is not None)
    selected: list[AgentProfile] = []
    for _ in range(count):
        _, profile = min(
            enumerate(profiles),
            key=lambda item: (usage[item[1].name], item[0]),
        )
        selected.append(profile)
        usage[profile.name] += 1
    return selected
