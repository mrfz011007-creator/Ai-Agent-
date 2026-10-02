def test_model_planner_reads_scoped_memory():
    from core.model_planner import ModelPlanService
    from core.memory_service import MemoryService
    import memory

    prompts = []
    memory.remember(
        "toolchain",
        "Gradle 9.8 is installed",
        memory_type="fact",
        project_id="launcher",
        source="test",
        provenance={"reason": "test"},
    )
    service = ModelPlanService(
        lambda prompt: prompts.append(prompt) or (
            '{"goal":"build APK","tasks":[{"task_id":"inspect","title":"Inspect","dependencies":[]}],'
            '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
        ),
        memory=MemoryService(),
    )
    service.propose("build APK", project_id="launcher")
    assert "Gradle 9.8 is installed" in prompts[0]
    assert "HISTORICAL MEMORY" in prompts[0]


def test_goal_execution_records_experience_and_retrieves_it(tmp_path, monkeypatch):
    from core.contracts import TaskStatus, ToolResult
    from core.runtime import AgentRuntime

    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"inspect workspace","tasks":[{"task_id":"inspect","title":"Inspect","dependencies":[]}],'
                '"acceptance_criteria":[{"type":"task_completed","task_id":"inspect"}]}'
            )
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        result = ToolResult(True, "success", proposal.tool, data={"ok": True}, evidence_id="ev-memory")
        runtime.evidence_store.record(
            evidence_id="ev-memory", task_id=task_id, attempt_id=attempt_id,
            tool=proposal.tool, action=proposal.action, result=result,
        )
        return result

    monkeypatch.setattr(runtime, "execute_model_proposal", fake_execute)

    plan, graph = runtime.run_goal(
        "inspect workspace", plan_id="plan-memory",
        project_id="ai-agent", context={"platform": "test"},
    )
    assert plan.status.value == "COMPLETED"
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED

    result = runtime.memory.retrieve(
        "task inspect execution", project_id="ai-agent",
        task_id="inspect", context={"platform": "test"},
    )
    assert result["status"] == "success"
    assert result["results"][0]["record"]["type"] == "experience"
    assert result["results"][0]["record"]["value"]["evidence_id"] == "ev-memory"


def test_memory_experience_survives_runtime_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    from core.runtime import AgentRuntime

    first = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    first.memory.record_experience(
        key="restart-experience", value={"outcome": "persisted"},
        task_id="task-1", source="test",
        provenance={"reason": "restart test"},
    )
    second = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    result = second.memory.retrieve("restart experience", task_id="task-1")
    assert result["status"] == "success"
    assert result["results"][0]["record"]["value"]["outcome"] == "persisted"
