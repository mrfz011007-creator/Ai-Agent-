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


def test_runtime_model_execution_uses_router_and_evidence(tmp_path):
    from core.runtime import AgentRuntime

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        __import__("core.contracts", fromlist=["Task"]).Task("T-RUN", "read")
    )
    task.status = __import__("core.contracts", fromlist=["TaskStatus"]).TaskStatus.READY
    runtime.task_manager.start("T-RUN")

    runtime.tool_router._registry_getter = lambda name: {
        "safe_tool": {"permission": "safe", "func": lambda **kwargs: {"success": True, "value": kwargs}}
    }.get(name)
    runtime.tool_router._policy = __import__("security.policy", fromlist=["PolicyEngine"]).PolicyEngine(runtime.tool_router._registry_getter)

    proposal = ModelExecutionService(
        lambda _: '{"tool":"safe_tool","action":"execute","arguments":{"x":"y"}}'
    ).propose("read")
    result = runtime.execute_model_proposal(proposal, task_id="T-RUN")
    assert result.success
    assert result.evidence_id is not None


def test_execution_prompt_receives_tool_catalog():
    prompts = []
    service = ModelExecutionService(
        lambda prompt: prompts.append(prompt) or '{"tool":"baca_file","action":"execute","arguments":{"nama":"README.md"}}',
        tool_catalog={"baca_file": "Read a workspace file", "patch_file": "Apply an exact patch"},
    )
    service.propose("inspect README")

    assert prompts
    assert "baca_file" in prompts[0]
    assert "patch_file" in prompts[0]


def test_execution_prompt_receives_bounded_previous_context():
    prompts = []
    service = ModelExecutionService(
        lambda prompt: prompts.append(prompt) or '{"tool":"patch_file","action":"execute","arguments":{"nama":"app.py","expected_sha256":"abc"}}'
    )
    service.propose(
        "edit app.py",
        context={"inspect": {"tool": "baca_file", "data": {"sha256": "abc", "isi": "print(1)"}}},
    )

    assert prompts
    assert '"sha256": "abc"' in prompts[0]
    assert len(prompts[0]) < 20000


def test_execution_context_is_truncated_before_model_prompt():
    prompts = []
    service = ModelExecutionService(
        lambda prompt: prompts.append(prompt) or '{"tool":"baca_file","action":"execute","arguments":{"nama":"app.py"}}'
    )
    service.propose("inspect", context={"large": "x" * 30000})

    assert "[CONTEXT_TRUNCATED]" in prompts[0]
    assert len(prompts[0]) < 20000


def test_execution_prompt_exposes_patch_snapshot_schema():
    prompts = []
    service = ModelExecutionService(
        lambda prompt: prompts.append(prompt) or '{"tool":"patch_file","action":"execute","arguments":{}}',
        tool_catalog={
            "patch_file": {
                "description": "Apply exact patch",
                "parameters": {
                    "properties": {
                        "nama": {"type": "string"},
                        "expected_sha256": {"type": "string"},
                    }
                },
                "permission": "confirm",
            }
        },
    )
    service.propose("edit file")

    assert '"expected_sha256"' in prompts[0]
