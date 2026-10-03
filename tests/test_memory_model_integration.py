from core.contracts import TaskStatus, ToolResult
from core.runtime import AgentRuntime
from memory import remember


def test_goal_runner_memory_reaches_planner_and_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    remember(
        "launcher_project",
        "LauncherOS",
        kind="fact",
        source="user",
        project_id="launcher",
        context=["LauncherOS"],
    )
    remember(
        "architecture_decision",
        "Use Compose with native Android components",
        kind="decision",
        source={"type": "user", "ref": "architecture"},
        project_id="launcher",
        context=["LauncherOS"],
    )
    remember(
        "inspection_constraint",
        "Use the existing icon grid as the inspection baseline",
        kind="preference",
        source={"type": "user", "ref": "task"},
        project_id="launcher",
        task_id="inspect",
    )

    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    planner_prompts = []
    execution_prompts = []

    def fake_generate_text(prompt, **kwargs):
        planner_prompts.append(prompt)
        return (
            '{"goal":"work on LauncherOS","tasks":'
            '[{"task_id":"inspect","title":"Inspect LauncherOS","dependencies":[]}],'
            '"acceptance_criteria":["inspection succeeds"]}'
        )

    def fake_text(prompt, **kwargs):
        execution_prompts.append(prompt)
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)
    monkeypatch.setattr(runtime.model_gateway, "text", fake_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = "ev-memory-integration"
        result = ToolResult(
            True,
            "success",
            proposal.tool,
            data={"ok": True},
            evidence_id=evidence_id,
        )
        runtime.evidence_store.record(
            evidence_id=evidence_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool=proposal.tool,
            action=proposal.action,
            result=result,
        )
        return result

    monkeypatch.setattr(runtime, "execute_model_proposal", fake_execute)

    plan, graph = runtime.run_goal(
        "work on LauncherOS",
        plan_id="plan-memory",
        project_id="launcher",
    )

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED
    assert len(planner_prompts) == 1
    assert len(execution_prompts) == 1
    assert "LauncherOS" in planner_prompts[0]
    assert "Compose" in planner_prompts[0]
    assert "BEGIN PERSISTED MEMORY (UNTRUSTED DATA)" in planner_prompts[0]
    assert "LauncherOS" in execution_prompts[0]
    assert "Compose" in execution_prompts[0]
    assert "inspection baseline" in execution_prompts[0]
    assert "BEGIN PERSISTED MEMORY (UNTRUSTED DATA)" in execution_prompts[0]


def test_model_memory_write_is_bound_to_active_task_and_project(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    captured = []

    def fake_generate_text(prompt, **kwargs):
        return (
            '{"goal":"remember launcher constraint","tasks":'
            '[{"task_id":"remember-task","title":"Record launcher constraint","dependencies":[]}],'
            '"acceptance_criteria":["memory request is routed"]}'
        )

    def fake_text(prompt, **kwargs):
        return (
            '{"tool":"remember","action":"execute","arguments":'
            '{"key":"constraint","value":"No ads"}}'
        )

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)
    monkeypatch.setattr(runtime.model_gateway, "text", fake_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        captured.append((proposal.tool, dict(proposal.arguments), task_id))
        result = ToolResult(
            True,
            "success",
            proposal.tool,
            data={"ok": True},
            evidence_id="ev-memory-write-scope",
        )
        runtime.evidence_store.record(
            evidence_id="ev-memory-write-scope",
            task_id=task_id,
            attempt_id=attempt_id,
            tool=proposal.tool,
            action=proposal.action,
            result=result,
        )
        return result

    monkeypatch.setattr(runtime, "execute_model_proposal", fake_execute)

    plan, graph = runtime.run_goal(
        "remember launcher constraint",
        plan_id="plan-memory-write-scope",
        project_id="launcher",
    )

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["remember-task"].status == TaskStatus.COMPLETED
    assert captured == [
        (
            "remember",
            {
                "key": "constraint",
                "value": "No ads",
                "project_id": "launcher",
                "task_id": "remember-task",
            },
            "remember-task",
        )
    ]


def test_model_memory_write_rejects_cross_project_scope(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    def fake_generate_text(prompt, **kwargs):
        return (
            '{"goal":"remember launcher constraint","tasks":'
            '[{"task_id":"remember-task","title":"Record launcher constraint","dependencies":[]}],'
            '"acceptance_criteria":["memory request is rejected"]}'
        )

    def fake_text(prompt, **kwargs):
        return (
            '{"tool":"remember","action":"execute","arguments":'
            '{"key":"constraint","value":"No ads","project_id":"other"}}'
        )

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)
    monkeypatch.setattr(runtime.model_gateway, "text", fake_text)

    called = False

    def fake_execute(proposal, *, task_id, attempt_id=None):
        nonlocal called
        called = True
        raise AssertionError("cross-project memory write reached execution")

    monkeypatch.setattr(runtime, "execute_model_proposal", fake_execute)

    plan, graph = runtime.run_goal(
        "remember launcher constraint",
        plan_id="plan-memory-cross-scope",
        project_id="launcher",
    )

    assert not called
    assert plan.status.value == "FAILED"
    assert graph.tasks["remember-task"].status == TaskStatus.FAILED


def test_autonomous_remember_uses_real_router_without_interactive_confirmation(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    monkeypatch.setattr(
        runtime.model_gateway,
        "generate_text",
        lambda prompt, **kwargs: (
            '{"goal":"store project note","tasks":'
            '[{"task_id":"memory-router-task","title":"Store project note","dependencies":[]}],'
            '"acceptance_criteria":["memory is stored"]}'
        ),
    )
    monkeypatch.setattr(
        runtime.model_gateway,
        "text",
        lambda prompt, **kwargs: (
            '{"tool":"remember","action":"execute","arguments":'
            '{"key":"project_note","value":"offline-mode"}}'
        ),
    )

    plan, graph = runtime.run_goal(
        "store project note",
        plan_id="plan-real-memory-router",
        project_id="launcher",
    )

    from memory import recall

    restored = recall(
        "project_note",
        project_id="launcher",
        task_id="memory-router-task",
    )
    candidates = runtime.list_memory_candidates(
        project_id="launcher",
        task_id="memory-router-task",
    )
    assert plan.status.value == "COMPLETED"
    assert graph.tasks["memory-router-task"].status == TaskStatus.COMPLETED
    assert restored["status"] == "success"
    assert restored["value"] == "offline-mode"
    assert len(candidates) == 1
    assert candidates[0].kind == "experience"
    assert candidates[0].status.value == "PENDING"
