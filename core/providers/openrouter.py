from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, Mapping


OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ERROR_BYTES = 16 * 1024


class OpenRouterError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OpenRouterProvider:
    """OpenRouter Chat Completions adapter using only the Python standard library."""

    name = "openrouter"

    def __init__(self, *, timeout_seconds: float = 45.0):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _as_mapping(value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        for method_name in ("model_dump", "to_dict", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    result = method()
                except Exception:
                    continue
                if isinstance(result, Mapping):
                    return result
        return {}

    @classmethod
    def _convert_tools(cls, configured_tools: Any) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        if not configured_tools:
            return converted
        if not isinstance(configured_tools, (list, tuple)):
            configured_tools = [configured_tools]

        for configured_tool in configured_tools:
            mapping = cls._as_mapping(configured_tool)
            declarations = mapping.get("function_declarations")
            if declarations is None:
                declarations = getattr(configured_tool, "function_declarations", None)
            if not declarations:
                continue
            for declaration in declarations:
                declaration_map = cls._as_mapping(declaration)
                name = declaration_map.get("name") or getattr(declaration, "name", None)
                description = declaration_map.get("description") or getattr(
                    declaration, "description", ""
                )
                parameters = declaration_map.get("parameters")
                if parameters is None:
                    parameters = getattr(declaration, "parameters", None)
                parameters_map = cls._as_mapping(parameters)
                if not isinstance(name, str) or not name:
                    continue
                if not parameters_map:
                    parameters_map = {"type": "object", "properties": {}}
                converted.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": cls._text(description),
                        "parameters": dict(parameters_map),
                    },
                })
        return converted

    def _request(
        self,
        api_key: str,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(api_key, str) or not api_key.strip():
            raise OpenRouterError("OPENROUTER_API_KEY is missing")
        if not isinstance(model, str) or not model.strip():
            raise OpenRouterError("OpenRouter model is empty", status_code=400)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(request_body) > MAX_REQUEST_BYTES:
            raise OpenRouterError("OpenRouter request exceeded the 1 MiB safety limit")
        request = urllib.request.Request(
            OPENROUTER_CHAT_COMPLETIONS_URL,
            data=request_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/mrfz011-creator/Ai-Agent-",
                "X-Title": "Fariz AI Agent",
            },
            method="POST",
        )

        try:
            # Fixed HTTPS endpoint; URL is not derived from model or user input.
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            try:
                body = error.read(MAX_ERROR_BYTES).decode("utf-8", errors="replace")
            except Exception:
                body = ""
            detail = body[:1000].replace(api_key, "[REDACTED]")
            raise OpenRouterError(
                f"OpenRouter HTTP {error.code}: {detail}",
                status_code=error.code,
            ) from error
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            detail = str(getattr(error, "reason", error))
            detail = detail[:300].replace(api_key, "[REDACTED]")
            raise OpenRouterError(
                f"OpenRouter network unavailable: {type(error).__name__}: {detail}"
            ) from error

        if len(raw) > MAX_RESPONSE_BYTES:
            raise OpenRouterError("OpenRouter response exceeded the 2 MiB safety limit")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenRouterError("OpenRouter returned invalid JSON") from error
        if not isinstance(result, dict):
            raise OpenRouterError("OpenRouter returned an unexpected response")
        if result.get("error"):
            error_value = result["error"]
            if isinstance(error_value, dict):
                message = self._text(error_value.get("message", "provider error"))
                code = error_value.get("code")
                status_code = code if isinstance(code, int) else None
            else:
                message = self._text(error_value)
                status_code = None
            safe_message = message[:1000].replace(api_key, "[REDACTED]")
            raise OpenRouterError(
                f"OpenRouter provider error: {safe_message}",
                status_code=status_code,
            )
        return result

    def text(
        self,
        api_key: str,
        *,
        prompt: str,
        system_instruction: str = "",
        model: str,
        response_mime_type: str | None = None,
    ) -> str:
        # The app parser validates JSON-only planner output. Avoid response_format:
        # some free models, including Nemotron 3 Ultra, do not support that mode.
        del response_mime_type
        messages: list[dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        result = self._request(api_key, model=model, messages=messages)
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterError("OpenRouter response contained no choices")
        message = choices[0].get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
        if not isinstance(content, str):
            raise OpenRouterError("OpenRouter response did not contain text")
        return content

    def generate(
        self,
        api_key: str,
        *,
        contents: Any,
        config: Any,
        model: str,
    ) -> Any:
        """Return a Gemini-shaped response for legacy callers; never executes tools."""
        config_map = self._as_mapping(config)
        system_instruction = config_map.get("system_instruction")
        if system_instruction is None:
            system_instruction = getattr(config, "system_instruction", "")
        if isinstance(system_instruction, (list, tuple)):
            system_instruction = " ".join(self._text(part) for part in system_instruction)

        messages: list[dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": self._text(system_instruction)})
        messages.append({
            "role": "user",
            "content": contents if isinstance(contents, str) else self._text(contents),
        })
        tools_value = config_map.get("tools")
        if tools_value is None:
            tools_value = getattr(config, "tools", None)
        tools = self._convert_tools(tools_value)
        result = self._request(api_key, model=model, messages=messages, tools=tools)
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterError("OpenRouter response contained no choices")

        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            raise OpenRouterError("OpenRouter response message was malformed")
        parts = []
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(SimpleNamespace(text=content, function_call=None))
        for call in message.get("tool_calls", []) or []:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            raw_arguments = function.get("arguments", "{}")
            try:
                arguments = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments)
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            parts.append(SimpleNamespace(
                text=None,
                function_call=SimpleNamespace(
                    name=function.get("name", ""),
                    args=arguments,
                ),
            ))
        candidate = SimpleNamespace(content=SimpleNamespace(parts=parts))
        return SimpleNamespace(
            candidates=[candidate],
            text=content if isinstance(content, str) else "",
        )
