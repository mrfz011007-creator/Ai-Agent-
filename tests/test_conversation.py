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



def test_cli_routes_normal_question_to_chat_without_goal_execution(monkeypatch):
    import agent

    class Runtime:
        def __init__(self):
            self.chat_messages = []
            self.goal_calls = []

        def chat(self, message):
            self.chat_messages.append(message)
            return "Jawaban langsung"

        def run_goal(self, message):
            self.goal_calls.append(message)
            raise AssertionError("normal question must not create a goal")

    runtime = Runtime()
    output = []
    monkeypatch.setattr(agent, "deteksi_intent", lambda _: None)

    result = agent.handle_request(runtime, "Jelaskan kemampuanmu", printer=lambda *x: output.append(" ".join(map(str, x))))

    assert result == "Jawaban langsung"
    assert runtime.chat_messages == ["Jelaskan kemampuanmu"]
    assert runtime.goal_calls == []
    assert any("Jawaban langsung" in line for line in output)


def test_cli_keeps_explicit_action_on_goal_runner(monkeypatch):
    import agent

    class Status:
        value = "COMPLETED"

    class Plan:
        plan_id = "plan-test"
        status = Status()

    class Graph:
        tasks = {}

    class Runtime:
        def __init__(self):
            self.goal_calls = []
            self.chat_messages = []

        def chat(self, message):
            self.chat_messages.append(message)
            return "chat"

        def run_goal(self, message):
            self.goal_calls.append(message)
            return Plan(), Graph()

    runtime = Runtime()
    monkeypatch.setattr(agent, "deteksi_intent", lambda _: None)
    output = []

    agent.handle_request(runtime, "Buat file catatan.txt", printer=lambda *x: output.append(" ".join(map(str, x))))

    assert runtime.goal_calls == ["Buat file catatan.txt"]
    assert runtime.chat_messages == []
    assert any("Goal selesai" in line for line in output)


def test_cli_ignores_empty_input_without_model_or_goal_call():
    import agent

    class Runtime:
        def chat(self, message):
            raise AssertionError("empty input must not call model")

        def run_goal(self, message):
            raise AssertionError("empty input must not create goal")

    assert agent.handle_request(Runtime(), "   ") is None
