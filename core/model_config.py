from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "gemini"
    model: str = DEFAULT_GEMINI_MODEL

    @classmethod
    def from_environment(cls) -> "ModelConfig":
        provider = os.environ.get("AI_AGENT_MODEL_PROVIDER", "gemini").strip().lower()
        model = os.environ.get("AI_AGENT_MODEL", DEFAULT_GEMINI_MODEL).strip()
        if not provider:
            raise ValueError("AI_AGENT_MODEL_PROVIDER cannot be empty")
        if not model:
            raise ValueError("AI_AGENT_MODEL cannot be empty")
        return cls(provider=provider, model=model)
