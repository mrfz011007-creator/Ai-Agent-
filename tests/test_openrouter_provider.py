import io
import json
import urllib.error

import pytest

from core.providers.openrouter import OpenRouterError, OpenRouterProvider
from core.model_gateway import FallbackModelGateway, ModelGateway, ProviderErrorKind


@pytest.fixture(autouse=True)
def _mock_opener_delegates_to_urlopen(monkeypatch):
    """Keep existing HTTP mocks while exercising the provider's custom opener."""

    class FakeOpener:
        def open(self, request, timeout):
            return urllib.request.urlopen(request, timeout=timeout)

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *_handlers: FakeOpener(),
    )


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.read_size = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        self.read_size = size
        return self.payload[:size]


def test_openrouter_text_sends_openai_compatible_request(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(b'{"choices":[{"message":{"content":"OK"}}]}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenRouterProvider(timeout_seconds=12)
    result = provider.text(
        "secret-value",
        prompt="same test prompt",
        system_instruction="system rules",
        model="nvidia/example:free",
    )

    assert result == "OK"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-value"
    assert captured["body"]["model"] == "nvidia/example:free"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "same test prompt"},
    ]
    assert captured["timeout"] == 12


def test_openrouter_does_not_send_response_format_for_incompatible_models(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(b'{"choices":[{"message":{"content":"{}"}}]}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    OpenRouterProvider().text(
        "secret",
        prompt="return json",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        response_mime_type="application/json",
    )
    assert "response_format" not in captured["body"]


def test_openrouter_http_error_is_classifiable(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"error":{"message":"rate limited"}}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OpenRouterError, match="HTTP 429") as caught:
        OpenRouterProvider().text(
            "secret",
            prompt="hello",
            model="nvidia/example:free",
        )
    assert caught.value.status_code == 429
    assert ModelGateway.classify_error(caught.value) == ProviderErrorKind.RATE_LIMIT


def test_openrouter_response_is_size_bounded(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse(b"x" * (2 * 1024 * 1024 + 5))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OpenRouterError, match="2 MiB"):
        OpenRouterProvider().text(
            "secret",
            prompt="hello",
            model="nvidia/example:free",
        )


def test_openrouter_generate_converts_gemini_function_declarations(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        payload = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "type": "function",
                        "function": {
                            "name": "baca_file",
                            "arguments": json.dumps({"nama": "README.md"}),
                        },
                    }],
                }
            }]
        }
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    class Declaration:
        name = "baca_file"
        description = "Read a file"
        parameters = {"type": "object", "properties": {"nama": {"type": "string"}}}

    class Tool:
        function_declarations = [Declaration()]

    class Config:
        tools = [Tool()]
        system_instruction = "Never execute tools automatically"

    response = OpenRouterProvider().generate(
        "secret",
        contents="read README",
        config=Config(),
        model="nvidia/example:free",
    )

    assert captured["body"]["tools"][0]["function"]["name"] == "baca_file"
    function_call = response.candidates[0].content.parts[0].function_call
    assert function_call.name == "baca_file"
    assert function_call.args == {"nama": "README.md"}


def test_provider_chain_uses_backup_then_gemini_only_on_retryable_errors():
    calls = []

    class Route:
        def __init__(self, name, result=None, error=None):
            self.name = name
            self.result = result
            self.error = error

        def generate_text(self, **kwargs):
            calls.append(self.name)
            if self.error:
                raise self.error
            return self.result

    gateway = FallbackModelGateway([
        Route("openrouter-primary", error=RuntimeError("503 unavailable")),
        Route("openrouter-backup", error=RuntimeError("429 rate limit")),
        Route("gemini-fallback", result="success"),
    ])

    assert gateway.generate_text(prompt="hello") == "success"
    assert calls == [
        "openrouter-primary",
        "openrouter-backup",
        "gemini-fallback",
    ]


def test_provider_chain_does_not_hide_bad_request():
    calls = []

    class Route:
        def __init__(self, name, error=None):
            self.name = name
            self.error = error

        def generate_text(self, **kwargs):
            calls.append(self.name)
            if self.error:
                raise self.error
            return "success"

    gateway = FallbackModelGateway([
        Route("primary", error=RuntimeError("400 malformed request")),
        Route("fallback"),
    ])

    with pytest.raises(RuntimeError, match="400"):
        gateway.generate_text(prompt="hello")
    assert calls == ["primary"]


def test_model_gateway_classifies_nested_connection_abort_as_unavailable():
    error = RuntimeError("MODEL_CREDENTIALS_EXHAUSTED")
    error.__cause__ = RuntimeError("[Errno 103] Software caused connection abort")
    assert ModelGateway.classify_error(error) == ProviderErrorKind.UNAVAILABLE


def test_provider_chain_falls_back_when_primary_model_is_not_found():
    calls = []

    class Route:
        def __init__(self, name, error=None):
            self.name = name
            self.error = error

        def generate_text(self, **kwargs):
            calls.append(self.name)
            if self.error:
                raise self.error
            return "backup success"

    gateway = FallbackModelGateway([
        Route("missing-model", error=OpenRouterError("not found", status_code=404)),
        Route("backup"),
    ])
    assert gateway.generate_text(prompt="hello") == "backup success"
    assert calls == ["missing-model", "backup"]


def test_openrouter_request_never_leaks_key_in_error(monkeypatch):
    key = "do-not-leak-this-key"

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(key.encode("utf-8")),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OpenRouterError) as caught:
        OpenRouterProvider().text(key, prompt="hello", model="nvidia/example:free")
    assert key not in str(caught.value)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_openrouter_redirect_handler_refuses_all_redirect_statuses(status):
    from core.providers.openrouter import _NoRedirectHandler

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": "Bearer do-not-forward"},
        method="POST",
    )
    handler = _NoRedirectHandler()

    redirected = handler.redirect_request(
        request,
        object(),
        status,
        "redirect",
        {},
        "https://attacker.example/collect",
    )

    assert redirected is None


def test_openrouter_builds_opener_with_redirects_disabled(monkeypatch):
    from core.providers.openrouter import _NoRedirectHandler

    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(b'{"choices":[{"message":{"content":"safe"}}]}')

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

    result = OpenRouterProvider(timeout_seconds=7).text(
        "private-api-key",
        prompt="hello",
        model="nvidia/example:free",
    )

    assert result == "safe"
    assert captured["timeout"] == 7
    assert any(isinstance(handler, _NoRedirectHandler) for handler in captured["handlers"])
    assert captured["request"].get_header("Authorization") == "Bearer private-api-key"
