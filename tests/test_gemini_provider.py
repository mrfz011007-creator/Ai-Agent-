from types import ModuleType, SimpleNamespace
import sys

import pytest

from core.providers.gemini import GeminiProvider


class FakeGenerateContentConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeAutomaticFunctionCallingConfig:
    def __init__(self, *, disable):
        self.disable = disable


def _install_fake_genai_types(monkeypatch):
    google_module = ModuleType("google")
    genai_module = ModuleType("google.genai")
    genai_module.types = SimpleNamespace(
        GenerateContentConfig=FakeGenerateContentConfig,
        AutomaticFunctionCallingConfig=FakeAutomaticFunctionCallingConfig,
    )
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)


@pytest.mark.parametrize(
    ("model_version", "expected_model", "verified"),
    [
        ("gemini-actual-v2", "gemini-actual-v2", True),
        (None, None, False),
    ],
)
def test_gemini_text_metadata_uses_provider_response_model(
    monkeypatch, model_version, expected_model, verified
):
    _install_fake_genai_types(monkeypatch)
    captured = {}

    response = SimpleNamespace(
        text="Gemini answer",
        model_version=model_version,
        response_id="google-response-1",
    )

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return response

    class FakeClient:
        def __init__(self):
            self.models = FakeModels()
            self.closed = False

        def close(self):
            self.closed = True

    client = FakeClient()
    provider = GeminiProvider(client_factory=lambda _key: client)

    result = provider.text_with_metadata(
        "secret",
        prompt="hello",
        system_instruction="system",
        model="gemini-configured-model",
    )

    assert result["text"] == "Gemini answer"
    assert result["actual_model"] == expected_model
    assert result["request_id"] == "google-response-1"
    assert client.closed is True
    assert captured["model"] == "gemini-configured-model"
    assert provider.text_with_metadata(
        "secret",
        prompt="hello",
        system_instruction="system",
        model="gemini-configured-model",
    )["actual_model"] == expected_model
