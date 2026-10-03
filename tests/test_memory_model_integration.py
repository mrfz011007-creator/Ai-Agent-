from core.contracts import TaskStatus, ToolResult
from core.runtime import AgentRuntime
from memory import remember


def test_goal_runner_memory_reaches_planner_and_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    remember("launcher_project", "LauncherOS")
    remember("architecture_decision", "Use Compose with native Android components")

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

    plan, graph = runtime.run_goal("work on LauncherOS", plan_id="plan-memory")

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED
    assert len(planner_prompts) == 1
    assert len(execution_prompts) == 1
    assert "LauncherOS" in planner_prompts[0]
    assert "Compose" in planner_prompts[0]
    assert "BEGIN PERSISTED MEMORY (UNTRUSTED DATA)" in planner_prompts[0]
    assert "LauncherOS" in execution_prompts[0]
    assert "Compose" in execution_prompts[0]
    assert "BEGIN PERSISTED MEMORY (UNTRUSTED DATA)" in execution_prompts[0]
