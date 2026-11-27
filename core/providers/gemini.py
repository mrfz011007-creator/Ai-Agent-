from __future__ import annotations

from typing import Any, Mapping


class GeminiProvider:
    """Gemini adapter. Credentials and rotation remain owned by ModelGateway."""

    name = "gemini"

    def __init__(self, client_factory=None, *, timeout_seconds: float = 45.0):
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 1 <= timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds must be between 1 and 120")
        self.timeout_seconds = float(timeout_seconds)
        self._client_factory = client_factory or self._default_client_factory

    def _default_client_factory(self, api_key: str) -> Any:
        from google import genai
        from google.genai import types

        # Google Gen AI HttpOptions.timeout is expressed in milliseconds.
        http_options = types.HttpOptions(
            timeout=int(self.timeout_seconds * 1000)
        )
        return genai.Client(api_key=api_key, http_options=http_options)

    def generate(
        self,
        api_key: str,
        *,
        contents: Any,
        config: Any,
        model: str,
    ) -> Any:
        client = self._client_factory(api_key)
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # Cleanup failure must not mask the provider response/error.
                    pass

    @staticmethod
    def _response_field(response: Any, *names: str) -> Any:
        if isinstance(response, Mapping):
            for name in names:
                value = response.get(name)
                if value is not None:
                    return value
        for name in names:
            value = getattr(response, name, None)
            if value is not None:
                return value
        return None

    def text_with_metadata(
        self,
        api_key: str,
        *,
        prompt: str,
        system_instruction: str,
        model: str,
        response_mime_type: str | None = None,
    ) -> dict[str, str | None]:
        from google.genai import types

        config_kwargs: Mapping[str, Any] = {
            "system_instruction": system_instruction,
            "automatic_function_calling": (
                types.AutomaticFunctionCallingConfig(disable=True)
            ),
        }
        if response_mime_type:
            config_kwargs = {
                **config_kwargs,
                "response_mime_type": response_mime_type,
            }

        response = self.generate(
            api_key,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
            model=model,
        )
        text = response.text or ""
        actual_model = self._response_field(response, "model_version", "modelVersion")
        request_id = self._response_field(response, "response_id", "responseId")
        return {
            "text": text,
            "actual_model": actual_model.strip() if isinstance(actual_model, str) and actual_model.strip() else None,
            "request_id": request_id.strip() if isinstance(request_id, str) and request_id.strip() else None,
        }

    def text(
        self,
        api_key: str,
        *,
        prompt: str,
        system_instruction: str,
        model: str,
        response_mime_type: str | None = None,
    ) -> str:
        """Backward-compatible text-only adapter."""
        return self.text_with_metadata(
            api_key,
            prompt=prompt,
            system_instruction=system_instruction,
            model=model,
            response_mime_type=response_mime_type,
        )["text"] or ""
