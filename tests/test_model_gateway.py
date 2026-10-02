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
    assert gateway.credentials[0].state == CredentialState.HEALTHY
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


def test_model_gateway_consumes_model_budget(monkeypatch):
    from core.budget import BudgetManager
    from core.contracts import Budget

    monkeypatch.setenv("K1", "secret-1")
    budget = BudgetManager(Budget(max_model_calls=1))
    gateway = ModelGateway(
        credentials=[Credential("k1", "K1")],
        client_factory=lambda secret: secret,
        budget=budget,
    )

    assert gateway.call(lambda client: client) == "secret-1"
    with pytest.raises(RuntimeError, match="MODEL_BUDGET_EXCEEDED"):
        gateway.call(lambda client: client)
    assert budget.budget.model_calls == 1


def test_exhausted_model_failure_can_put_running_task_in_waiting(tmp_path):
    from core.contracts import Task, TaskStatus
    from core.runtime import AgentRuntime

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task("MODEL-R1", "model failure"))
    task.status = TaskStatus.READY
    runtime.task_manager.start("MODEL-R1")

    result = runtime.handle_model_failure(
        "MODEL-R1", RuntimeError("MODEL_CREDENTIALS_EXHAUSTED")
    )

    assert result.status == TaskStatus.WAITING
    assert runtime.task_manager.get("MODEL-R1").status == TaskStatus.WAITING


def test_gateway_structured_generation_uses_provider_and_model_config(monkeypatch):
    from core.model_config import ModelConfig

    monkeypatch.setenv("K1", "secret-1")

    class FakeProvider:
        name = "gemini"

        def generate(self, secret, *, contents, config, model):
            assert secret == "secret-1"
            assert contents == "hello"
            assert model == "test-model"
            return {"ok": True}

    gateway = ModelGateway(
        credentials=[Credential("k1", "K1")],
        provider=FakeProvider(),
        config=ModelConfig(provider="gemini", model="test-model"),
    )

    assert gateway.generate(contents="hello", config={"x": 1}) == {"ok": True}


def test_gateway_rejects_provider_mismatch():
    from core.model_config import ModelConfig

    class FakeProvider:
        name = "other"

    with pytest.raises(ValueError, match="provider mismatch"):
        ModelGateway(
            credentials=[],
            provider=FakeProvider(),
            config=ModelConfig(provider="gemini", model="test-model"),
        )


def test_unavailable_error_rotates_with_bounded_backoff(monkeypatch):
    monkeypatch.setenv("K1", "secret-1")
    monkeypatch.setenv("K2", "secret-2")
    now = [100.0]
    calls = []

    gateway = ModelGateway(
        credentials=[Credential("k1", "K1"), Credential("k2", "K2")],
        client_factory=lambda secret: secret,
        clock=lambda: now[0],
        cooldown_seconds=60,
    )

    def invoke(client):
        calls.append(client)
        if client == "secret-1":
            raise RuntimeError("503 unavailable")
        return "ok"

    assert gateway.call(invoke) == "ok"
    assert calls == ["secret-1", "secret-2"]
    assert gateway.credentials[0].state == CredentialState.COOLDOWN
    assert gateway.credentials[0].cooldown_until == 102.0


def test_missing_credentials_fail_without_provider_call(monkeypatch):
    monkeypatch.delenv("K1", raising=False)
    calls = []
    gateway = ModelGateway(
        credentials=[Credential("k1", "K1")],
        client_factory=lambda secret: calls.append(secret),
    )

    with pytest.raises(RuntimeError, match="NO_MODEL_CREDENTIAL_AVAILABLE"):
        gateway.call(lambda _: "must not run")

    assert calls == []


def test_model_budget_failure_does_not_rotate_or_retry(monkeypatch):
    from core.budget import BudgetManager
    from core.contracts import Budget

    monkeypatch.setenv("K1", "secret-1")
    monkeypatch.setenv("K2", "secret-2")
    budget = BudgetManager(Budget(max_model_calls=1))
    calls = []
    gateway = ModelGateway(
        credentials=[Credential("k1", "K1"), Credential("k2", "K2")],
        client_factory=lambda secret: secret,
        budget=budget,
    )

    assert gateway.call(lambda client: calls.append(client) or "ok") == "ok"
    with pytest.raises(RuntimeError, match="MODEL_BUDGET_EXCEEDED"):
        gateway.call(lambda client: calls.append(client) or "should-not-run")

    assert calls == ["secret-1"]
    assert [c.state for c in gateway.credentials] == [
        CredentialState.HEALTHY,
        CredentialState.UNKNOWN,
    ]



@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_model_gateway_rejects_invalid_max_attempts(value):
    with pytest.raises(ValueError, match="max_attempts must be a positive integer"):
        ModelGateway(
            credentials=[Credential("k1", "K1")],
            client_factory=lambda secret: secret,
            max_attempts=value,
        )


def test_model_gateway_accepts_explicit_positive_max_attempts():
    gateway = ModelGateway(
        credentials=[Credential("k1", "K1"), Credential("k2", "K2")],
        client_factory=lambda secret: secret,
        max_attempts=1,
    )
    assert gateway.max_attempts == 1
