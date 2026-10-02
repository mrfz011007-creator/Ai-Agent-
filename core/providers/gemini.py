from __future__ import annotations

from typing import Any, Mapping

from core.model_config import ModelConfig


class GeminiProvider:
    """Gemini adapter. Credentials and rotation remain owned by ModelGateway."""

    name = "gemini"

    def __init__(self, client_factory=None):
        self._client_factory = client_factory or self._default_client_factory

    @staticmethod
    def _default_client_factory(api_key: str) -> Any:
        from google import genai
        return genai.Client(api_key=api_key)

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
