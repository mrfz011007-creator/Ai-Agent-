from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_OPENROUTER_BACKUP_MODEL = "nvidia/nemotron-3.5-super-vl:free"


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "openrouter"
    model: str = DEFAULT_OPENROUTER_MODEL

    @classmethod
    def from_environment(cls) -> "ModelConfig":
        provider = os.environ.get("AI_AGENT_MODEL_PROVIDER", "openrouter").strip().lower()
        if not provider:
            raise ValueError("AI_AGENT_MODEL_PROVIDER cannot be empty")
        default_model = DEFAULT_GEMINI_MODEL if provider == "gemini" else DEFAULT_OPENROUTER_MODEL
        model = os.environ.get("AI_AGENT_MODEL", default_model).strip()
        if not model:
            raise ValueError("AI_AGENT_MODEL cannot be empty")
        if provider not in {"openrouter", "auto", "gemini"}:
            raise ValueError(f"Unsupported model provider: {provider}")
        return cls(provider=provider, model=model)
