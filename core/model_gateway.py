from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from core.budget import BudgetManager
from core.model_config import ModelConfig
from core.providers.gemini import GeminiProvider


class CredentialState(str, Enum):
    HEALTHY = "HEALTHY"
    COOLDOWN = "COOLDOWN"
    INVALID = "INVALID"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class ProviderErrorKind(str, Enum):
    RATE_LIMIT = "RATE_LIMIT"
    AUTH = "AUTH"
    BAD_REQUEST = "BAD_REQUEST"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class Credential:
    name: str
    secret_env: str
    state: CredentialState = CredentialState.UNKNOWN
    cooldown_until: float = 0.0
    failures: int = 0


class ModelGateway:
    """Single model-entry boundary with bounded credential rotation."""

    def __init__(
        self,
        *,
        credentials: list[Credential],
        client_factory: Callable[[str], Any] | None = None,
        provider: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: float = 60.0,
        max_attempts: int | None = None,
        budget: BudgetManager | None = None,
        config: ModelConfig | None = None,
    ):
        if provider is not None and client_factory is not None:
            raise ValueError("Choose provider or client_factory, not both")
        self.credentials = credentials
        self.provider = provider
        self.client_factory = client_factory
        self.clock = clock
        self.cooldown_seconds = cooldown_seconds
        self.max_attempts = max_attempts or max(1, len(credentials))
        self.budget = budget
        self.config = config or ModelConfig.from_environment()
        provider_name = getattr(provider, "name", None)
        if provider_name is not None and self.config.provider != provider_name:
            raise ValueError(
                f"Model provider mismatch: config={self.config.provider}, "
                f"provider={provider_name}"
            )

    @staticmethod
    def classify_error(error: Exception) -> ProviderErrorKind:
        message = str(error).lower()
        if any(x in message for x in ("401", "403", "invalid api key", "unauthorized")):
            return ProviderErrorKind.AUTH
        if any(x in message for x in ("400", "invalid argument", "malformed")):
            return ProviderErrorKind.BAD_REQUEST
        if any(x in message for x in ("429", "resource_exhausted", "rate limit", "quota")):
            return ProviderErrorKind.RATE_LIMIT
        if any(x in message for x in ("503", "unavailable", "high demand", "timeout")):
            return ProviderErrorKind.UNAVAILABLE
        return ProviderErrorKind.UNKNOWN

    def _available(self) -> list[Credential]:
        now = self.clock()
        available = []
        for credential in self.credentials:
            if credential.state in (CredentialState.INVALID, CredentialState.DISABLED):
                continue
            if credential.state == CredentialState.COOLDOWN and credential.cooldown_until > now:
                continue
            if credential.state == CredentialState.COOLDOWN:
                credential.state = CredentialState.HEALTHY
            if os.getenv(credential.secret_env):
                available.append(credential)
        return available

    def call(self, invoke: Callable[[Any], Any]) -> Any:
        """Low-level provider-neutral boundary retained for deterministic tests."""
        attempts = 0
        last_error: Exception | None = None
        tried: set[str] = set()

        while attempts < self.max_attempts:
            available = [c for c in self._available() if c.name not in tried]
            if not available:
                break

            credential = available[0]
            tried.add(credential.name)
            attempts += 1
            if self.budget is not None:
                self.budget.reserve_model_call()
            credential.state = CredentialState.HEALTHY
            secret = os.getenv(credential.secret_env)
            if not secret:
                credential.state = CredentialState.UNKNOWN
                continue

            try:
                if self.provider is not None:
                    result = invoke(self.provider, secret)
                else:
                    client = self.client_factory(secret)
                    result = invoke(client)
                credential.state = CredentialState.HEALTHY
                credential.failures = 0
                return result
            except Exception as error:
                last_error = error
                kind = self.classify_error(error)
                credential.failures += 1

                if kind == ProviderErrorKind.RATE_LIMIT:
                    credential.state = CredentialState.COOLDOWN
                    credential.cooldown_until = self.clock() + self.cooldown_seconds
                    continue
                if kind == ProviderErrorKind.AUTH:
                    credential.state = CredentialState.INVALID
                    continue
                if kind == ProviderErrorKind.BAD_REQUEST:
                    raise
                if kind == ProviderErrorKind.UNAVAILABLE:
                    credential.state = CredentialState.COOLDOWN
                    credential.cooldown_until = self.clock() + min(
                        self.cooldown_seconds, 2 ** credential.failures
                    )
                    continue
                raise

        if last_error is not None:
            raise RuntimeError("MODEL_CREDENTIALS_EXHAUSTED") from last_error
        raise RuntimeError("NO_MODEL_CREDENTIAL_AVAILABLE")

    def generate_text(
        self,
        *,
        prompt: str,
        system_instruction: str,
        response_mime_type: str | None = None,
    ) -> str:
        """High-level model API used by planners and execution services."""
        if self.provider is None:
            raise RuntimeError("MODEL_PROVIDER_NOT_CONFIGURED")

        def invoke(provider, secret):
            return provider.text(
                secret,
                prompt=prompt,
                system_instruction=system_instruction,
                model=self.config.model,
                response_mime_type=response_mime_type,
            )

        return self.call(invoke)

    def generate(
        self,
        *,
        contents: Any,
        config: Any,
    ) -> Any:
        """Provider-neutral structured generation endpoint."""
        if self.provider is None:
            raise RuntimeError("MODEL_PROVIDER_NOT_CONFIGURED")

        def invoke(provider, secret):
            return provider.generate(
                secret,
                contents=contents,
                config=config,
                model=self.config.model,
            )

        return self.call(invoke)


def gemini_credentials() -> list[Credential]:
    return [
        Credential(f"GEMINI_API_KEY_{i}", f"GEMINI_API_KEY_{i}")
        for i in range(1, 4)
    ]


def create_gemini_gateway(
    *,
    budget: BudgetManager | None = None,
    config: ModelConfig | None = None,
) -> ModelGateway:
    config = config or ModelConfig.from_environment()
    if config.provider != "gemini":
        raise ValueError(f"Unsupported model provider: {config.provider}")
    return ModelGateway(
        credentials=gemini_credentials(),
        provider=GeminiProvider(),
        budget=budget,
        config=config,
    )
