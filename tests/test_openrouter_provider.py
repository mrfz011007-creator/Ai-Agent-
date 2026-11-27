import io
import json
import urllib.error

import pytest

from core.providers.openrouter import OpenRouterError, OpenRouterProvider
from core.model_gateway import (
    Credential,
    FallbackModelGateway,
    ModelCallResult,
    ModelGateway,
    ProviderErrorKind,
)
from core.model_config import ModelConfig


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



def test_provider_chain_skips_same_provider_after_invalid_key():
    from types import SimpleNamespace

    calls = []

    class Route:
        def __init__(self, name, provider, error=None, result=None):
            self.name = name
            self.provider = SimpleNamespace(name=provider)
            self.error = error
            self.result = result

        def generate_text(self, **kwargs):
            calls.append(self.name)
            if self.error:
                raise self.error
            return self.result

    gateway = FallbackModelGateway([
        Route(
            "openrouter-primary", "openrouter",
            error=OpenRouterError("invalid API key", status_code=401),
        ),
        Route("openrouter-backup", "openrouter", result="must-not-run"),
        Route("gemini-fallback", "gemini", result="fallback-ok"),
    ])

    assert gateway.generate_text(prompt="hello") == "fallback-ok"
    assert calls == ["openrouter-primary", "gemini-fallback"]


def test_provider_chain_keeps_model_specific_forbidden_fallback_available():
    from types import SimpleNamespace

    calls = []

    class Route:
        def __init__(self, name, error=None, result=None):
            self.name = name
            self.provider = SimpleNamespace(name="openrouter")
            self.error = error
            self.result = result

        def generate_text(self, **kwargs):
            calls.append(self.name)
            if self.error:
                raise self.error
            return self.result

    gateway = FallbackModelGateway([
        Route("model-forbidden", error=OpenRouterError("model access forbidden", status_code=403)),
        Route("allowed-backup", result="backup-ok"),
    ])
    assert gateway.generate_text(prompt="hello") == "backup-ok"
    assert calls == ["model-forbidden", "allowed-backup"]


def test_gemini_default_client_uses_explicit_timeout(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from core.providers.gemini import GeminiProvider

    captured = {}

    class FakeHttpOptions:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

    def fake_client(*, api_key, http_options):
        captured["api_key"] = api_key
        captured["http_options"] = http_options
        return "client"

    google_module = ModuleType("google")
    genai_module = ModuleType("google.genai")
    genai_module.Client = fake_client
    genai_module.types = SimpleNamespace(HttpOptions=FakeHttpOptions)
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)

    client = GeminiProvider(timeout_seconds=17.5)._default_client_factory("secret")
    assert client == "client"
    assert captured["api_key"] == "secret"
    assert captured["timeout"] == 17500


def test_gemini_rejects_invalid_timeout():
    from core.providers.gemini import GeminiProvider
    with pytest.raises(ValueError, match="timeout_seconds"):
        GeminiProvider(timeout_seconds=0)



def test_gemini_client_is_closed_after_request():
    from core.providers.gemini import GeminiProvider

    class FakeModels:
        def generate_content(self, **kwargs):
            assert kwargs == {"model": "test-model", "contents": "hello", "config": {}}
            return "response"

    class FakeClient:
        def __init__(self):
            self.models = FakeModels()
            self.closed = False

        def close(self):
            self.closed = True

    client = FakeClient()
    provider = GeminiProvider(client_factory=lambda _key: client)
    assert provider.generate(
        "key", contents="hello", config={}, model="test-model"
    ) == "response"
    assert client.closed is True



def test_openrouter_reports_model_and_request_id_from_provider_response(monkeypatch):
    payload = {
        "id": "req-openrouter-123",
        "model": "nvidia/nemotron-3.5-super-vl:free",
        "choices": [{"message": {"content": "served"}}],
    }

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(json.dumps(payload).encode("utf-8")),
    )

    result = OpenRouterProvider().text_with_metadata(
        "secret",
        prompt="hello",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
    )

    assert result == {
        "text": "served",
        "actual_model": "nvidia/nemotron-3.5-super-vl:free",
        "request_id": "req-openrouter-123",
    }


def test_openrouter_marks_actual_model_unavailable_when_provider_omits_it(monkeypatch):
    payload = {"choices": [{"message": {"content": "served"}}]}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(json.dumps(payload).encode("utf-8")),
    )

    result = OpenRouterProvider().text_with_metadata(
        "secret", prompt="hello", model="requested/model"
    )

    assert result["text"] == "served"
    assert result["actual_model"] is None


def test_model_gateway_keeps_legacy_string_api_and_returns_provider_metadata(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class Provider:
        name = "openrouter"

        def text_with_metadata(self, api_key, **kwargs):
            assert api_key == "test-key"
            assert kwargs["model"] == "requested/model"
            return {
                "text": "hello",
                "actual_model": "provider/actual-model",
                "request_id": "req-42",
            }

    gateway = ModelGateway(
        credentials=[Credential("OPENROUTER_API_KEY", "OPENROUTER_API_KEY")],
        provider=Provider(),
        config=ModelConfig(provider="openrouter", model="requested/model"),
        max_attempts=1,
    )

    metadata = gateway.generate_text_with_metadata("prompt")
    assert metadata == ModelCallResult(
        text="hello",
        provider="openrouter",
        requested_model="requested/model",
        actual_model="provider/actual-model",
        request_id="req-42",
    )
    assert gateway.generate_text("prompt") == "hello"


def test_fallback_gateway_reports_the_route_that_actually_succeeded():
    from types import SimpleNamespace

    calls = []

    class Route:
        def __init__(self, name, provider, model, response=None, error=None):
            self.provider = SimpleNamespace(name=provider)
            self.config = SimpleNamespace(provider=provider, model=model)
            self.response = response
            self.error = error
            self.name = name

        def generate_text_with_metadata(self, **kwargs):
            calls.append(self.name)
            if self.error:
                raise self.error
            return self.response

    backup_result = ModelCallResult(
        text="from backup",
        provider="openrouter",
        requested_model="backup/requested",
        actual_model="backup/served",
        request_id="req-backup",
    )
    gateway = FallbackModelGateway([
        Route("primary", "openrouter", "primary/model", error=RuntimeError("503 unavailable")),
        Route("backup", "openrouter", "backup/requested", response=backup_result),
    ])

    result = gateway.generate_text_with_metadata("prompt")

    assert result == backup_result
    assert calls == ["primary", "backup"]
    assert gateway.generate_text("prompt") == "from backup"


def test_fallback_legacy_route_is_reported_unverified_not_as_actual_model():
    from types import SimpleNamespace

    class LegacyRoute:
        provider = SimpleNamespace(name="openrouter")
        config = SimpleNamespace(provider="openrouter", model="configured/model")

        def generate_text(self, **kwargs):
            return "legacy response"

    result = FallbackModelGateway([LegacyRoute()]).generate_text_with_metadata("prompt")

    assert result.text == "legacy response"
    assert result.provider == "openrouter"
    assert result.requested_model == "configured/model"
    assert result.actual_model is None
    assert result.identity_verified is False
