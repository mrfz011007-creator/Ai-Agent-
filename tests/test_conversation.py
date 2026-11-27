from core.conversation import ConversationService
from core.model_gateway import ModelCallResult
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
    import json

    payload = json.loads(gateway.calls[0]["prompt"])
    assert payload["current_user_message"] == "Jelaskan kemampuanmu"
    assert payload["conversation_history"] == []
    assert "openrouter/test-model" in gateway.calls[0]["system_instruction"]


def test_chat_system_instruction_forbids_claiming_tool_execution():
    gateway = FakeGateway()
    ConversationService(gateway).answer("Apa kemampuanmu?")

    instruction = gateway.calls[0]["system_instruction"]
    assert "Do not create task plans" in instruction
    assert "or claim to have executed actions" in instruction


def test_chat_prompt_does_not_guess_current_serving_model_from_configuration():
    gateway = FakeGateway()
    ConversationService(gateway).answer("Model apa yang menangani pesan ini?")

    instruction = gateway.calls[0]["system_instruction"].lower()
    assert "do not guess the serving provider or actual model" in instruction
    assert "displays provider-reported identity in a separate runtime" in instruction
    assert "label them as configuration" in instruction


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
    assert not is_action_request("Buat apa AI Agent ini?")
    assert not is_action_request("Bisa buat apa AI Agent ini?")
    assert is_action_request("Bisa kamu buat file catatan.txt")
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



def test_conversation_returns_provider_metadata_separately_from_answer():
    class MetadataGateway:
        def generate_text_with_metadata(self, **kwargs):
            return ModelCallResult(
                text="Jawaban dari model",
                provider="openrouter",
                requested_model="nvidia/primary",
                actual_model="nvidia/served-model",
                request_id="req-chat-1",
            )

    reply = ConversationService(MetadataGateway()).answer_with_metadata("Halo")

    assert reply.answer == "Jawaban dari model"
    assert reply.provider == "openrouter"
    assert reply.requested_model == "nvidia/primary"
    assert reply.actual_model == "nvidia/served-model"
    assert reply.identity_verified is True


def test_conversation_keeps_model_identity_unverified_when_provider_omits_it():
    class MetadataGateway:
        def generate_text_with_metadata(self, **kwargs):
            return ModelCallResult(
                text="Jawaban dari fallback",
                provider="gemini",
                requested_model="gemini/configured",
                actual_model=None,
            )

    reply = ConversationService(MetadataGateway()).answer_with_metadata("Halo")

    assert reply.answer == "Jawaban dari fallback"
    assert reply.provider == "gemini"
    assert reply.requested_model == "gemini/configured"
    assert reply.actual_model is None
    assert reply.identity_verified is False


def test_cli_displays_provider_reported_model_metadata_without_changing_return_type(monkeypatch):
    import agent
    from core.conversation import ConversationReply

    class Runtime:
        def chat_with_metadata(self, message):
            return ConversationReply(
                answer="Jawaban",
                provider="openrouter",
                requested_model="primary/model",
                actual_model="served/model",
                request_id="req-cli-1",
            )

        def run_goal(self, message):
            raise AssertionError("normal chat must not create a goal")

    monkeypatch.setattr(agent, "deteksi_intent", lambda _: None)
    output = []
    result = agent.handle_request(
        Runtime(),
        "Model apa yang melayani?",
        printer=lambda *args: output.append(" ".join(map(str, args))),
    )

    assert result == "Jawaban"
    rendered = "\n".join(output)
    assert "Metadata runtime (di luar jawaban AI)" in rendered
    assert "Provider : openrouter" in rendered
    assert "Model aktual : served/model" in rendered
    assert "Terverifikasi dari metadata provider" in rendered
    assert r"\nProvider" not in rendered



def test_cli_labels_missing_provider_model_metadata_as_unverified(monkeypatch):
    import agent
    from core.conversation import ConversationReply

    class Runtime:
        def chat_with_metadata(self, message):
            return ConversationReply(
                answer="Saya tidak menerima metadata model aktual.",
                provider="gemini",
                requested_model="gemini/configured",
                actual_model=None,
            )

        def run_goal(self, message):
            raise AssertionError("normal chat must not create a goal")

    monkeypatch.setattr(agent, "deteksi_intent", lambda _: None)
    output = []
    agent.handle_request(
        Runtime(),
        "Model apa yang menangani pesan ini?",
        printer=lambda *args: output.append(" ".join(map(str, args))),
    )

    rendered = "\n".join(output)
    assert "Metadata runtime (di luar jawaban AI)" in rendered
    assert "Provider : gemini" in rendered
    assert "Model diminta : gemini/configured" in rendered
    assert "Belum dapat diverifikasi dari metadata provider" in rendered
    assert "Model aktual :" not in rendered
