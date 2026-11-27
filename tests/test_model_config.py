import pytest

from core.model_config import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    ModelConfig,
)
from core.model_gateway import (
    FallbackModelGateway,
    ModelGateway,
    create_model_gateway,
)


def test_model_config_defaults_to_openrouter_primary(monkeypatch):
    monkeypatch.delenv("AI_AGENT_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("AI_AGENT_MODEL", raising=False)
    assert ModelConfig.from_environment() == ModelConfig(
        provider="openrouter",
        model=DEFAULT_OPENROUTER_MODEL,
    )


def test_model_config_keeps_direct_gemini_mode(monkeypatch):
    monkeypatch.setenv("AI_AGENT_MODEL_PROVIDER", "gemini")
    monkeypatch.delenv("AI_AGENT_MODEL", raising=False)
    assert ModelConfig.from_environment() == ModelConfig(
        provider="gemini",
        model=DEFAULT_GEMINI_MODEL,
    )


def test_model_config_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("AI_AGENT_MODEL_PROVIDER", "unknown")
    with pytest.raises(ValueError, match="Unsupported model provider"):
        ModelConfig.from_environment()


def test_default_gateway_has_openrouter_primary_backup_and_gemini_fallback(monkeypatch):
    monkeypatch.delenv("AI_AGENT_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("AI_AGENT_MODEL", raising=False)
    monkeypatch.delenv("AI_AGENT_OPENROUTER_BACKUP_MODEL", raising=False)
    monkeypatch.delenv("AI_AGENT_FALLBACK_GEMINI_MODEL", raising=False)

    gateway = create_model_gateway()

    assert isinstance(gateway, FallbackModelGateway)
    assert len(gateway.routes) == 3
    primary, backup, gemini = gateway.routes
    assert isinstance(primary, ModelGateway)
    assert primary.provider.name == "openrouter"
    assert primary.config.model == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert backup.config.model == "nvidia/nemotron-3.5-super-vl:free"
    assert gemini.provider.name == "gemini"
    assert gemini.config.model == "gemini-3.8-flash"


def test_gateway_can_use_gemini_directly_without_openrouter(monkeypatch):
    monkeypatch.setenv("AI_AGENT_MODEL_PROVIDER", "gemini")
    monkeypatch.delenv("AI_AGENT_MODEL", raising=False)

    gateway = create_model_gateway()

    assert isinstance(gateway, ModelGateway)
    assert gateway.provider.name == "gemini"
    assert gateway.config.model == "gemini-3.8-flash"


def test_openrouter_backup_can_be_disabled(monkeypatch):
    monkeypatch.delenv("AI_AGENT_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("AI_AGENT_MODEL", raising=False)
    monkeypatch.setenv("AI_AGENT_OPENROUTER_BACKUP_MODEL", "")

    gateway = create_model_gateway()

    assert isinstance(gateway, FallbackModelGateway)
    assert [route.provider.name for route in gateway.routes] == [
        "openrouter",
        "gemini",
    ]
