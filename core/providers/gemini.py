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
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    def text(
        self,
        api_key: str,
        *,
        prompt: str,
        system_instruction: str,
        model: str,
        response_mime_type: str | None = None,
    ) -> str:
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
        return response.text or ""
