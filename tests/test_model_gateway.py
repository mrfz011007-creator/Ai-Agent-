import os

import pytest

from core.model_gateway import (
    Credential,
    CredentialState,
    ModelGateway,
    ProviderErrorKind,
)


def test_model_gateway_rotates_on_rate_limit(monkeypatch):
    monkeypatch.setenv("K1", "secret-1")
    monkeypatch.setenv("K2", "secret-2")
    calls = []

    def factory(secret):
        return secret

    def invoke(client):
        calls.append(client)
        if client == "secret-1":
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return "ok"

    gateway = ModelGateway(
        credentials=[Credential("k1", "K1"), Credential("k2", "K2")],
        client_factory=factory,
        cooldown_seconds=60,
    )
    assert gateway.call(invoke) == "ok"
    assert calls == ["secret-1", "secret-2"]


def test_auth_error_invalidates_credential(monkeypatch):
    monkeypatch.setenv("K1", "secret-1")

    gateway = ModelGateway(
        credentials=[Credential("k1", "K1")],
        client_factory=lambda secret: secret,
    )

    with pytest.raises(RuntimeError, match="MODEL_CREDENTIALS_EXHAUSTED"):
        gateway.call(lambda _: (_ for _ in ()).throw(RuntimeError("401 invalid api key")))

    assert gateway.credentials[0].state == CredentialState.INVALID


def test_bad_request_does_not_rotate(monkeypatch):
    monkeypatch.setenv("K1", "secret-1")
    monkeypatch.setenv("K2", "secret-2")
    calls = []

    gateway = ModelGateway(
        credentials=[Credential("k1", "K1"), Credential("k2", "K2")],
        client_factory=lambda secret: secret,
    )

    def invoke(client):
        calls.append(client)
        raise RuntimeError("400 malformed request")

    with pytest.raises(RuntimeError, match="400"):
        gateway.call(invoke)

    assert calls == ["secret-1"]
    assert gateway.credentials[0].state == CredentialState.UNKNOWN
    assert ModelGateway.classify_error(RuntimeError("429 quota")) == ProviderErrorKind.RATE_LIMIT


def test_all_credentials_exhausted(monkeypatch):
    monkeypatch.setenv("K1", "secret-1")
    monkeypatch.setenv("K2", "secret-2")
    gateway = ModelGateway(
        credentials=[Credential("k1", "K1"), Credential("k2", "K2")],
        client_factory=lambda secret: secret,
    )

    with pytest.raises(RuntimeError, match="MODEL_CREDENTIALS_EXHAUSTED"):
        gateway.call(lambda _: (_ for _ in ()).throw(RuntimeError("429 quota")))

    assert all(c.state == CredentialState.COOLDOWN for c in gateway.credentials)
