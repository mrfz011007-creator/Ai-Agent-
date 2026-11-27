from core.conversation import ConversationService
from core.request_router import is_action_request


class FakeGateway:
    def __init__(self, response="Jawaban langsung"):
        self.response = response
        self.calls = []
        self.config = type("Config", (), {"provider": "openrouter", "model": "test-model"})()

    def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_regular_question_uses_direct_chat_not_task_planner():
    gateway = FakeGateway()
    service = ConversationService(gateway)

    assert service.answer("Jelaskan kemampuanmu") == "Jawaban langsung"
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["prompt"] == "Jelaskan kemampuanmu"
    assert "openrouter/test-model" in gateway.calls[0]["system_instruction"]


def test_chat_system_instruction_forbids_claiming_tool_execution():
    gateway = FakeGateway()
    ConversationService(gateway).answer("Apa kemampuanmu?")

    instruction = gateway.calls[0]["system_instruction"]
    assert "Do not create task plans" in instruction
    assert "do not claim to have executed actions" in instruction


def test_chat_rejects_empty_and_oversized_messages_without_model_call():
    gateway = FakeGateway()
    service = ConversationService(gateway)

    for message in ("", "   ", "x" * 12001):
        try:
            service.answer(message)
            assert False, "invalid message must be rejected"
        except ValueError:
            pass

    assert gateway.calls == []


def test_chat_rejects_empty_model_response():
    gateway = FakeGateway(response="  ")
    try:
        ConversationService(gateway).answer("Halo")
        assert False, "empty model response must fail"
    except RuntimeError as error:
        assert "EMPTY_RESPONSE" in str(error)


def test_model_description_lists_fallback_chain_without_claiming_active_model():
    primary = FakeGateway()
    backup = FakeGateway()
    backup.config = type("Config", (), {"provider": "gemini", "model": "backup-model"})()
    gateway = type("Fallback", (), {"routes": [primary, backup], "generate_text": primary.generate_text})()

    service = ConversationService(gateway)
    assert "openrouter/test-model" in service.model_description
    assert "gemini/backup-model" in service.model_description


def test_action_router_is_conservative():
    assert not is_action_request("Jelaskan kemampuanmu dan model AI yang digunakan")
    assert not is_action_request("Bagaimana cara membuat file Python?")
    assert not is_action_request("Apa perbedaan Linux dan Android?")
    assert is_action_request("Buat file catatan.txt")
    assert is_action_request("Tolong periksa status repository")
    assert is_action_request("Jalankan pengujian")
