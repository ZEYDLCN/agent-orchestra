import pytest

from orchestrator.agent_profiles import (
    AgentProfileConfigurationError,
    parse_agent_profiles,
    select_profiles_for_scale,
)


def test_empty_string_returns_no_profiles():
    assert parse_agent_profiles("") == []
    assert parse_agent_profiles("   ") == []


def test_parses_single_profile():
    profiles = parse_agent_profiles("qwen|ollama|qwen2.5:3b")
    assert len(profiles) == 1
    assert profiles[0].name == "qwen"
    assert profiles[0].provider == "ollama"
    assert profiles[0].model == "qwen2.5:3b"


def test_model_tag_with_colon_is_preserved():
    # this is exactly why "|" and not ":" separates fields
    profiles = parse_agent_profiles("qwen|ollama|qwen2.5:3b")
    assert profiles[0].model == "qwen2.5:3b"


def test_parses_multiple_profiles():
    profiles = parse_agent_profiles(
        "qwen|ollama|qwen2.5:3b;claude|anthropic|claude-sonnet-5;gpt|openai|gpt-4o-mini"
    )
    assert [p.name for p in profiles] == ["qwen", "claude", "gpt"]
    assert [p.provider for p in profiles] == ["ollama", "anthropic", "openai"]
    assert [p.model for p in profiles] == ["qwen2.5:3b", "claude-sonnet-5", "gpt-4o-mini"]


def test_profile_without_model_defaults_to_none():
    profiles = parse_agent_profiles("mock-agent|mock")
    assert profiles[0].provider == "mock"
    assert profiles[0].model is None


def test_ignores_blank_entries_from_trailing_semicolon():
    profiles = parse_agent_profiles("qwen|ollama|qwen2.5:3b;;")
    assert len(profiles) == 1


def test_whitespace_around_fields_is_trimmed():
    profiles = parse_agent_profiles(" qwen | ollama | qwen2.5:3b ")
    assert profiles[0].name == "qwen"
    assert profiles[0].provider == "ollama"
    assert profiles[0].model == "qwen2.5:3b"


@pytest.mark.parametrize(
    "raw",
    [
        "qwen",
        "|ollama|qwen2.5:3b",
        "qwen||qwen2.5:3b",
        "qwen|unknown|model",
        "qwen|mock;qwen|ollama|qwen2.5:3b",
        "qwen|ollama|model|extra",
    ],
)
def test_rejects_malformed_or_ambiguous_profiles(raw):
    with pytest.raises(AgentProfileConfigurationError):
        parse_agent_profiles(raw)


def test_provider_is_normalized_for_environment_and_model_selection():
    profile = parse_agent_profiles("qwen|OLLAMA|qwen2.5:3b")[0]
    assert profile.provider == "ollama"


def test_incremental_scale_balances_profiles_instead_of_restarting_at_first():
    profiles = parse_agent_profiles("local|ollama|qwen2.5:3b;reviewer|mock")

    first = select_profiles_for_scale(profiles, [], 1)
    second = select_profiles_for_scale(profiles, [first[0].name], 1)

    assert [first[0].name, second[0].name] == ["local", "reviewer"]


def test_scale_up_after_scale_down_fills_the_missing_profile():
    profiles = parse_agent_profiles("local|ollama|qwen2.5:3b;reviewer|mock")

    selected = select_profiles_for_scale(profiles, ["reviewer"], 1)

    assert selected[0].name == "local"
