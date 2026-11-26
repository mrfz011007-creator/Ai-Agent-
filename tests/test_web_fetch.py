from __future__ import annotations

import socket

import pytest

from execution import web_fetch


class FakeResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self.body = body

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, _limit=-1):
        return self.body


def test_url_parser_requires_https_and_rejects_credentials_and_ports():
    for url, expected in (
        ("http://example.com", "HTTPS_REQUIRED"),
        ("https://user:pass@example.com/", "URL_CREDENTIALS_DENIED"),
        ("https://example.com:8443/", "NON_STANDARD_PORT_DENIED"),
        ("https://localhost/", "LOCAL_HOST_DENIED"),
        ("https://example.com/" + "x" * 2100, "URL_INVALID"),
    ):
        with pytest.raises(web_fetch.WebFetchError, match=expected):
            web_fetch._parse_https_url(url)


def test_dns_rejects_private_and_mixed_public_private_answers(monkeypatch):
    private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [public, private])
    with pytest.raises(web_fetch.WebFetchError, match="NON_PUBLIC_ADDRESS_DENIED"):
        web_fetch._public_addresses("example.com")


def test_dns_rejects_link_local_and_private_ip_literals():
    for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1"):
        with pytest.raises(web_fetch.WebFetchError, match="NON_PUBLIC_ADDRESS_DENIED"):
            web_fetch._public_addresses(address)


def test_fetch_pins_validated_ip_and_returns_bounded_text(monkeypatch):
    seen = []
    response = FakeResponse(
        200,
        {"Content-Type": "text/plain; charset=utf-8", "Content-Length": "5"},
        b"hello",
    )

    class FakeConnection:
        def __init__(self, host, address):
            seen.append(("connect", host, address))
        def request(self, method, target, headers):
            seen.append(("request", method, target, headers))
        def getresponse(self):
            return response
        def close(self):
            pass

    monkeypatch.setattr(web_fetch, "_public_addresses", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(web_fetch, "_PinnedHTTPSConnection", FakeConnection)
    result = web_fetch.fetch_url("https://example.com/path")

    assert result["success"] is True
    assert result["text"] == "hello"
    assert seen[0] == ("connect", "example.com", "93.184.216.34")
    assert seen[1][1:3] == ("GET", "/path")
    assert "Authorization" not in seen[1][3]
    assert "Cookie" not in seen[1][3]


def test_fetch_revalidates_redirect_and_rejects_private_destination(monkeypatch):
    calls = []
    response = FakeResponse(302, {"Location": "https://127.0.0.1/admin"})

    class FakeConnection:
        def __init__(self, host, address):
            calls.append((host, address))
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return response
        def close(self):
            pass

    monkeypatch.setattr(web_fetch, "_public_addresses", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(web_fetch, "_PinnedHTTPSConnection", FakeConnection)
    with pytest.raises(web_fetch.WebFetchError, match="NON_PUBLIC_ADDRESS_DENIED"):
        web_fetch.fetch_url("https://example.com/redirect")
    assert calls == [("example.com", "93.184.216.34")]


def test_fetch_rejects_non_text_and_oversized_responses(monkeypatch):
    class FakeConnection:
        def __init__(self, host, address):
            pass
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return FakeResponse(200, {"Content-Type": "application/octet-stream"}, b"binary")
        def close(self):
            pass

    monkeypatch.setattr(web_fetch, "_public_addresses", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(web_fetch, "_PinnedHTTPSConnection", FakeConnection)
    with pytest.raises(web_fetch.WebFetchError, match="CONTENT_TYPE_NOT_TEXT"):
        web_fetch.fetch_url("https://example.com/file")


def test_fetch_tool_is_registered_as_confirmed_read_only_network_capability():
    from registry import TOOL_REGISTRY

    metadata = TOOL_REGISTRY["fetch_url"]
    assert metadata["capabilities"] == ["network.read"]
    assert metadata["permission"] == "confirm"
    assert metadata["parameters"]["additionalProperties"] is False


def test_router_schema_denial_is_persisted_as_security_evidence(tmp_path):
    from core.budget import Budget, BudgetManager
    from core.contracts import ToolRequest
    from core.state_store import StateStore
    from execution.router import ToolRouter
    from security.policy import PolicyEngine
    from verification.evidence import EvidenceStore

    registry = {
        "fetch_url": {
            "permission": "confirm",
            "capabilities": ["network.read"],
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            "func": lambda **_kwargs: pytest.fail("invalid request must not execute"),
        }
    }
    evidence = EvidenceStore(StateStore(tmp_path / "audit.sqlite3"))
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=evidence,
        confirmation=lambda *_: True,
    )
    result = router.execute(ToolRequest(
        "fetch_url", "execute", {"url": "https://example.com", "unexpected": "x"}
    ))
    assert result.success is False
    assert result.status == "schema_invalid"
    assert result.evidence_id
    record = evidence.get(result.evidence_id)
    assert record is not None
    assert record.result_status == "schema_invalid"


def test_fetch_rejects_response_body_over_hard_limit(monkeypatch):
    class FakeConnection:
        def __init__(self, host, address):
            pass
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return FakeResponse(
                200,
                {"Content-Type": "text/plain"},
                b"x" * (web_fetch.MAX_RESPONSE_BYTES + 1),
            )
        def close(self):
            pass

    monkeypatch.setattr(web_fetch, "_public_addresses", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(web_fetch, "_PinnedHTTPSConnection", FakeConnection)
    with pytest.raises(web_fetch.WebFetchError, match="RESPONSE_TOO_LARGE"):
        web_fetch.fetch_url("https://example.com/large")
