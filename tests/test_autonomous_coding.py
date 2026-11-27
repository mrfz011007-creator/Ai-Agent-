"""Stage 8: bounded autonomous coding through the real guarded tool router."""

import ast
import hashlib
import json

from core.plan import PlanStatus
from core.runtime import AgentRuntime
from core.contracts import TaskStatus


def _contract(tool, capability, task_id):
    return {
        "objective": f"Complete the bounded {tool} step for {task_id}",
        "allowed_tools": [tool],
        "allowed_capabilities": [capability],
        "max_tool_calls": 1,
        "retry_limit": 0,
        "evidence_required": True,
        "completion_conditions": [{"type": "tool_success", "task_id": task_id, "tool": tool}],
    }


def test_agent_completes_bounded_code_change_and_static_validation_via_router(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("AI_AGENT_REFLECT_ON_SUCCESS", "0")

    starter = "def add(a, b):\n    return a - b\n"
    updated = "def add(a, b):\n    return a + b\n"
    (workspace / "calculator.py").write_text(starter, encoding="utf-8")
    starter_hash = hashlib.sha256(starter.encode("utf-8")).hexdigest()

    runtime = AgentRuntime.create(state_path=workspace / "state.sqlite3")
    plan_payload = {
        "goal": "Correct calculator.add and validate its Python syntax",
        "tasks": [
            {
                "task_id": "inspect-source",
                "title": "Read calculator.py and obtain its current snapshot",
                "dependencies": [],
                "execution_contract": _contract("baca_file", "workspace.read", "inspect-source"),
            },
            {
                "task_id": "edit-source",
                "title": "Change calculator.add to use addition",
                "dependencies": ["inspect-source"],
                "execution_contract": _contract("tulis_file", "workspace.write", "edit-source"),
            },
            {
                "task_id": "validate-source",
                "title": "Validate calculator.py syntax and required add definition",
                "dependencies": ["edit-source"],
                "execution_contract": _contract("validasi_python", "workspace.read", "validate-source"),
            },
        ],
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    }
    execution_proposals = [
        {"tool": "baca_file", "action": "execute", "arguments": {"nama": "calculator.py"}},
        {
            "tool": "tulis_file",
            "action": "execute",
            "arguments": {
                "nama": "calculator.py",
                "isi": updated,
                "expected_sha256": starter_hash,
            },
        },
        {
            "tool": "validasi_python",
            "action": "execute",
            "arguments": {"nama": "calculator.py", "fungsi_wajib": ["add"]},
        },
    ]
    prompts = []

    monkeypatch.setattr(runtime.model_gateway, "generate_text", lambda _prompt, **_kwargs: json.dumps(plan_payload))

    def propose(_prompt, **_kwargs):
        prompts.append(_prompt)
        return json.dumps(execution_proposals[len(prompts) - 1])

    monkeypatch.setattr(runtime.model_gateway, "text", propose)
    confirmations = []
    runtime.tool_router._confirmation = lambda tool, args: confirmations.append((tool, dict(args))) or True

    plan, graph = runtime.run_goal(
        "Correct calculator.add and validate its Python syntax",
        plan_id="stage8-autonomous-code",
        project_id="stage8-project",
        context={"workspace": "isolated-test-workspace"},
    )

    assert plan.status == PlanStatus.COMPLETED
    assert [graph.tasks[key].status for key in (
        "inspect-source", "edit-source", "validate-source"
    )] == [TaskStatus.COMPLETED] * 3
    assert [tool for tool, _ in confirmations] == [
        "tulis_file",
    ]
    assert len(prompts) == 3

    final_source = (workspace / "calculator.py").read_text(encoding="utf-8")
    parsed = ast.parse(final_source)
    add_function = next(
        node for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name == "add"
    )
    assert isinstance(add_function.body[0].value.op, ast.Add)
    assert final_source == updated

    for task_id, tool in (
        ("inspect-source", "baca_file"),
        ("edit-source", "tulis_file"),
        ("validate-source", "validasi_python"),
    ):
        task = graph.tasks[task_id]
        assert task.attempts == 1
        assert task.tool_calls == 1
        evidence = runtime.state_store.load_evidence_for_task(task_id)
        assert len(evidence) == 1
        assert evidence[0]["success"] is True
        assert evidence[0]["tool"] == tool
        assert evidence[0]["attempt_id"] == f"{task_id}:attempt:1"


def test_python_validator_rejects_syntax_errors_and_missing_definitions(
    tmp_path, monkeypatch
):
    import tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    (workspace / "broken.py").write_text("def add(:\n    return 1\n", encoding="utf-8")
    syntax = tools.validasi_python("broken.py", ["add"])
    assert syntax["success"] is False
    assert syntax["code"] == "PYTHON_SYNTAX_INVALID"

    (workspace / "valid.py").write_text("VALUE = 3\n", encoding="utf-8")
    missing = tools.validasi_python("valid.py", ["add"])
    assert missing["success"] is False
    assert missing["code"] == "REQUIRED_DEFINITION_MISSING"

    valid = tools.validasi_python("valid.py")
    assert valid["success"] is True
    assert valid["code"] == "PYTHON_SYNTAX_VALID"


def test_python_validator_never_executes_source_and_rejects_workspace_escape(
    tmp_path, monkeypatch
):
    import tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def hidden():\n    return True\n", encoding="utf-8")
    marker = workspace / "executed.txt"
    (workspace / "safe.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\ndef run():\n    return True\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    result = tools.validasi_python("safe.py", ["run"])
    assert result["success"] is True
    assert not marker.exists()

    escaped = tools.validasi_python("../outside.py")
    assert escaped["success"] is False
    assert not marker.exists()
