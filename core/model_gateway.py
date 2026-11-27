from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from core.budget import BudgetManager
from core.model_config import ModelConfig
from core.providers.gemini import GeminiProvider
from core.providers.openrouter import OpenRouterProvider


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



@dataclass(frozen=True)
class ModelCallResult:
    """Text plus provider metadata returned outside the model-generated content."""

    text: str
    provider: str | None
    requested_model: str | None
    actual_model: str | None
    request_id: str | None = None

    @property
    def identity_verified(self) -> bool:
        return self.actual_model is not None


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
        if max_attempts is not None and (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise ValueError("max_attempts must be a positive integer")
        self.max_attempts = max(1, len(credentials)) if max_attempts is None else max_attempts
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
        messages = []
        status_codes: list[int] = []
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            messages.append(str(current).lower())
            status_code = getattr(current, "status_code", None)
            if isinstance(status_code, str) and status_code.isdigit():
                status_code = int(status_code)
            if isinstance(status_code, int):
                status_codes.append(status_code)
            current = current.__cause__ or current.__context__
        message = " ".join(messages)
        if any(code in {401, 403} for code in status_codes) or any(
            x in message for x in ("401", "403", "invalid api key", "unauthorized")
        ):
            return ProviderErrorKind.AUTH
        if any(code in {402, 429} for code in status_codes) or any(
            x in message for x in ("429", "resource_exhausted", "rate limit", "quota")
        ):
            return ProviderErrorKind.RATE_LIMIT
        if any(code in {404, 408, 409, 425, 500, 502, 503, 504} for code in status_codes):
            return ProviderErrorKind.UNAVAILABLE
        if any(x in message for x in ("400", "invalid argument", "malformed")):
            return ProviderErrorKind.BAD_REQUEST
        if any(x in message for x in (
            "503", "502", "504", "unavailable", "high demand", "timeout",
            "timed out", "connection aborted", "connection reset", "connection",
            "network", "url error", "no_model_credential_available",
            "model_credentials_exhausted",
        )):
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
            secret = os.getenv(credential.secret_env, "")
            if secret.strip():
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

    def text(self, prompt: str, *, system_instruction: str = "", response_mime_type: str | None = None) -> str:
        """Compatibility adapter for planner/execution callbacks."""
        return self.generate_text(
            prompt=prompt,
            system_instruction=system_instruction,
            response_mime_type=response_mime_type,
        )

    def generate_text_with_metadata(
        self,
        prompt: str,
        *,
        system_instruction: str = "",
        response_mime_type: str | None = None,
    ) -> ModelCallResult:
        """Return text and provider-reported identity without changing text APIs."""
        if self.provider is None:
            raise RuntimeError("MODEL_PROVIDER_NOT_CONFIGURED")

        def invoke(provider, secret):
            metadata_method = getattr(provider, "text_with_metadata", None)
            if callable(metadata_method):
                return metadata_method(
                    secret,
                    prompt=prompt,
                    system_instruction=system_instruction,
                    model=self.config.model,
                    response_mime_type=response_mime_type,
                )
            return {
                "text": provider.text(
                    secret,
                    prompt=prompt,
                    system_instruction=system_instruction,
                    model=self.config.model,
                    response_mime_type=response_mime_type,
                ),
                "actual_model": None,
                "request_id": None,
            }

        response = self.call(invoke)
        if not isinstance(response, dict):
            raise RuntimeError("MODEL_PROVIDER_RESPONSE_INVALID")
        text = response.get("text")
        if not isinstance(text, str):
            raise RuntimeError("MODEL_PROVIDER_RESPONSE_INVALID")
        actual_model = response.get("actual_model")
        request_id = response.get("request_id")
        return ModelCallResult(
            text=text,
            provider=getattr(self.provider, "name", self.config.provider),
            requested_model=self.config.model,
            actual_model=actual_model.strip() if isinstance(actual_model, str) and actual_model.strip() else None,
            request_id=request_id.strip() if isinstance(request_id, str) and request_id.strip() else None,
        )

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str = "",
        response_mime_type: str | None = None,
    ) -> str:
        """Backward-compatible text-only API used by planners and execution services."""
        return self.generate_text_with_metadata(
            prompt,
            system_instruction=system_instruction,
            response_mime_type=response_mime_type,
        ).text

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
    if config is None:
        config = ModelConfig(
            provider="gemini",
            model=os.environ.get("AI_AGENT_GEMINI_MODEL", "gemini-3.8-flash").strip(),
        )
    if config.provider != "gemini":
        raise ValueError(f"Unsupported model provider: {config.provider}")
    timeout = float(os.environ.get("AI_AGENT_MODEL_TIMEOUT_SECONDS", "45"))
    return ModelGateway(
        credentials=gemini_credentials(),
        provider=GeminiProvider(timeout_seconds=timeout),
        budget=budget,
        config=config,
    )


def create_openrouter_gateway(
    *,
    budget: BudgetManager | None = None,
    config: ModelConfig | None = None,
) -> ModelGateway:
    config = config or ModelConfig.from_environment()
    if config.provider not in {"openrouter", "auto"}:
        raise ValueError(f"Unsupported model provider: {config.provider}")
    timeout = float(os.environ.get("AI_AGENT_MODEL_TIMEOUT_SECONDS", "45"))
    return ModelGateway(
        credentials=[Credential("OPENROUTER_API_KEY", "OPENROUTER_API_KEY")],
        provider=OpenRouterProvider(timeout_seconds=timeout),
        budget=budget,
        config=ModelConfig(provider="openrouter", model=config.model),
        max_attempts=1,
    )


class FallbackModelGateway:
    """Bounded provider chain; model outputs remain untrusted tool proposals."""

    def __init__(self, routes: list[ModelGateway]):
        if not routes:
            raise ValueError("At least one model route is required")
        self.routes = routes

    @staticmethod
    def _provider_name(route) -> str | None:
        return getattr(getattr(route, "provider", None), "name", None)

    @staticmethod
    def _should_skip_provider(error: Exception) -> bool:
        """Avoid repeating requests to a provider when its credential cannot work."""
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            status_code = getattr(current, "status_code", None)
            message = str(current).lower()
            if status_code == 401 or any(
                marker in message
                for marker in (
                    "invalid api key",
                    "no_model_credential_available",
                )
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    def _invoke(self, method: str, **kwargs):
        last_error: Exception | None = None
        skipped_providers: set[str] = set()
        for route in self.routes:
            provider_name = self._provider_name(route)
            if provider_name and provider_name in skipped_providers:
                continue
            try:
                return getattr(route, method)(**kwargs)
            except Exception as error:
                last_error = error
                kind = ModelGateway.classify_error(error)
                if kind not in {
                    ProviderErrorKind.AUTH,
                    ProviderErrorKind.RATE_LIMIT,
                    ProviderErrorKind.UNAVAILABLE,
                }:
                    raise
                if provider_name and self._should_skip_provider(error):
                    skipped_providers.add(provider_name)
        if last_error is not None:
            raise RuntimeError("MODEL_PROVIDERS_EXHAUSTED") from last_error
        raise RuntimeError("NO_MODEL_PROVIDER_AVAILABLE")

    def generate_text_with_metadata(
        self,
        prompt: str,
        *,
        system_instruction: str = "",
        response_mime_type: str | None = None,
    ) -> ModelCallResult:
        """Return metadata from the exact fallback route that served the request."""
        last_error: Exception | None = None
        skipped_providers: set[str] = set()
        for route in self.routes:
            provider_name = self._provider_name(route)
            if provider_name and provider_name in skipped_providers:
                continue
            try:
                method = getattr(route, "generate_text_with_metadata", None)
                if callable(method):
                    result = method(
                        prompt=prompt,
                        system_instruction=system_instruction,
                        response_mime_type=response_mime_type,
                    )
                else:
                    # Compatibility for simple/custom routes implementing only
                    # the long-standing string-returning API.
                    result = route.generate_text(
                        prompt=prompt,
                        system_instruction=system_instruction,
                        response_mime_type=response_mime_type,
                    )
                if isinstance(result, ModelCallResult):
                    return result
                if isinstance(result, str):
                    config = getattr(route, "config", None)
                    provider = provider_name or getattr(config, "provider", None)
                    requested_model = getattr(config, "model", None)
                    return ModelCallResult(
                        text=result,
                        provider=provider,
                        requested_model=requested_model,
                        actual_model=None,
                    )
                raise RuntimeError("MODEL_PROVIDER_RESPONSE_INVALID")
            except Exception as error:
                last_error = error
                kind = ModelGateway.classify_error(error)
                if kind not in {
                    ProviderErrorKind.AUTH,
                    ProviderErrorKind.RATE_LIMIT,
                    ProviderErrorKind.UNAVAILABLE,
                }:
                    raise
                if provider_name and self._should_skip_provider(error):
                    skipped_providers.add(provider_name)
        if last_error is not None:
            raise RuntimeError("MODEL_PROVIDERS_EXHAUSTED") from last_error
        raise RuntimeError("NO_MODEL_PROVIDER_AVAILABLE")

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str = "",
        response_mime_type: str | None = None,
    ) -> str:
        return self.generate_text_with_metadata(
            prompt,
            system_instruction=system_instruction,
            response_mime_type=response_mime_type,
        ).text

    def text(
        self,
        prompt: str,
        *,
        system_instruction: str = "",
        response_mime_type: str | None = None,
    ) -> str:
        return self.generate_text(
            prompt=prompt,
            system_instruction=system_instruction,
            response_mime_type=response_mime_type,
        )

    def generate(self, *, contents: Any, config: Any) -> Any:
        return self._invoke("generate", contents=contents, config=config)


def create_model_gateway(
    *,
    budget: BudgetManager | None = None,
    config: ModelConfig | None = None,
):
    """OpenRouter primary → one model backup → Gemini final fallback."""
    config = config or ModelConfig.from_environment()
    if config.provider == "gemini":
        return create_gemini_gateway(budget=budget, config=config)
    if config.provider not in {"openrouter", "auto"}:
        raise ValueError(f"Unsupported model provider: {config.provider}")

    routes = [create_openrouter_gateway(budget=budget, config=config)]
    backup_model = os.environ.get(
        "AI_AGENT_OPENROUTER_BACKUP_MODEL",
        "nvidia/nemotron-3.5-super-vl:free",
    ).strip()
    if backup_model and backup_model != config.model:
        routes.append(create_openrouter_gateway(
            budget=budget,
            config=ModelConfig(provider="openrouter", model=backup_model),
        ))

    gemini_model = os.environ.get(
        "AI_AGENT_FALLBACK_GEMINI_MODEL",
        "gemini-3.8-flash",
    ).strip()
    if gemini_model:
        routes.append(create_gemini_gateway(
            budget=budget,
            config=ModelConfig(provider="gemini", model=gemini_model),
        ))
    return FallbackModelGateway(routes)
