from core.model_execution import ModelExecutionService
from core.contracts import ToolRequest, ToolResult


class FakeRouter:
    def __init__(self):
        self.request = None

    def execute(self, request):
        self.request = request
        return ToolResult(True, "success", request.tool, data={"ok": True})


def test_execution_proposal_is_untrusted_intent_only():
    service = ModelExecutionService(
        lambda _: '{"tool":"baca_file","action":"execute","arguments":{"nama":"README.md"}}'
    )
    proposal = service.propose("inspect README")
    assert proposal.tool == "baca_file"
    assert proposal.arguments["nama"] == "README.md"


def test_execution_proposal_rejects_non_execute_action():
    service = ModelExecutionService(
        lambda _: '{"tool":"run_command","action":"delete","arguments":{}}'
    )
    try:
        service.propose("delete")
        assert False, "non-execute action must be rejected"
    except ValueError:
        pass


def test_execution_proposal_never_executes_directly():
    router = FakeRouter()
    service = ModelExecutionService(
        lambda _: '{"tool":"safe_tool","action":"execute","arguments":{"x":"y"}}'
    )
    proposal = service.propose("do safe work")
    result = service.execute(proposal, router, task_id="T1", attempt_id="T1:attempt:1")
    assert result.success
    assert isinstance(router.request, ToolRequest)
    assert router.request.source == "model"
    assert router.request.task_id == "T1"
    assert router.request.attempt_id == "T1:attempt:1"
