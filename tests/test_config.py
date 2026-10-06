"""Tests for puriyudha.config.PipelineConfig."""
from puriyudha.config import DEFAULT_LLM_ENABLED, LLM_ENABLED_ENV_VAR, PipelineConfig


def test_default_llm_enabled_is_false():
    """CLAUDE.md invariant #6: the LLM fallback is disabled by default --
    this is a designed property, so it gets its own test naming the exact
    constant, not just an implicit default-argument value."""
    assert DEFAULT_LLM_ENABLED is False


def test_pipeline_config_default_matches_default_llm_enabled():
    config = PipelineConfig()
    assert config.llm_enabled is DEFAULT_LLM_ENABLED


def test_pipeline_config_can_be_explicitly_enabled():
    config = PipelineConfig(llm_enabled=True)
    assert config.llm_enabled is True


def test_pipeline_config_is_frozen():
    config = PipelineConfig()
    try:
        config.llm_enabled = True
        assert False, "PipelineConfig should be immutable (frozen dataclass)"
    except Exception as exc:
        assert type(exc).__name__ in ("FrozenInstanceError", "AttributeError")


def test_from_env_defaults_to_disabled_when_unset(monkeypatch):
    monkeypatch.delenv(LLM_ENABLED_ENV_VAR, raising=False)
    assert PipelineConfig.from_env().llm_enabled is False


def test_from_env_enabled_only_for_the_literal_value_one(monkeypatch):
    monkeypatch.setenv(LLM_ENABLED_ENV_VAR, "1")
    assert PipelineConfig.from_env().llm_enabled is True


def test_from_env_any_other_value_stays_disabled(monkeypatch):
    for value in ("true", "True", "yes", "0", "enabled", ""):
        monkeypatch.setenv(LLM_ENABLED_ENV_VAR, value)
        assert PipelineConfig.from_env().llm_enabled is False, f"value {value!r} unexpectedly enabled it"
