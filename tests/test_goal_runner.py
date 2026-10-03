from core.contracts import TaskStatus, ToolResult
from core.runtime import AgentRuntime


def test_runtime_run_goal_completes_through_execution_and_acceptance(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"inspect workspace","tasks":'
                '[{"task_id":"inspect","title":"Inspect workspace","dependencies":[]}],'
                '"acceptance_criteria":[{"type":"task_completed","task_id":"inspect"}]}'
            )
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = "ev-goal-inspect"
        result = ToolResult(True, "success", proposal.tool, data={"ok": True}, evidence_id=evidence_id)
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

    plan, graph = runtime.run_goal("inspect workspace", plan_id="plan-run")

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED
    assert runtime.state_store.load_plan("plan-run")["status"] == "COMPLETED"
    evidence = runtime.state_store.load_evidence_for_task("inspect")
    assert evidence
    assert all(item["success"] for item in evidence)
    assert all(item["attempt_id"] == "inspect:attempt:1" for item in evidence)


def test_runtime_run_goal_respects_step_boundary(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"bounded","tasks":'
                '[{"task_id":"one","title":"One","dependencies":[]},'
                '{"task_id":"two","title":"Two","dependencies":["one"]}],'
                '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
            )
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = f"ev-{task_id}"
        result = ToolResult(True, "success", proposal.tool, data={"ok": True}, evidence_id=evidence_id)
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

    plan, graph = runtime.run_goal("bounded", plan_id="plan-bound", max_steps=1)

    assert plan.status.value == "WAITING"
    assert graph.tasks["one"].status == TaskStatus.COMPLETED
    assert graph.tasks["two"].status == TaskStatus.PENDING
    assert runtime.state_store.load_plan("plan-bound")["status"] == "WAITING"


def test_runtime_resume_goal_is_plan_scoped(tmp_path):
    from core.contracts import Task
    from core.plan import Planner

    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    proposal_one = Planner().propose(
        "plan one", [Task("one-build", "Build one")]
    )
    plan_one, _ = runtime.orchestrator.materialize(proposal_one, "plan-one")
    runtime.task_manager.start("one-build")

    proposal_two = Planner().propose(
        "plan two", [Task("two-build", "Build two")]
    )
    plan_two, _ = runtime.orchestrator.materialize(proposal_two, "plan-two")
    runtime.task_manager.start("two-build")

    fresh = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    restored_one = fresh.orchestrator.restore_graph(plan_one.plan_id)
    assert restored_one is not None
    fresh.recovery_manager.recover_tasks(tuple(restored_one[1].tasks))

    assert fresh.orchestrator.restore_graph(plan_one.plan_id)[1].tasks["one-build"].status == TaskStatus.WAITING
    assert fresh.orchestrator.restore_graph(plan_two.plan_id)[1].tasks["two-build"].status == TaskStatus.RUNNING


def test_goal_runner_passes_prior_tool_result_to_dependent_task(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    prompts = []

    def fake_generate_text(prompt, **kwargs):
        return (
            '{"goal":"inspect then validate","tasks":'
            '[{"task_id":"inspect","title":"Inspect source","dependencies":[]},'
            '{"task_id":"validate","title":"Validate inspected source","dependencies":["inspect"]}],'
            '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
        )

    def fake_text(prompt, **kwargs):
        prompts.append(prompt)
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)
    monkeypatch.setattr(runtime.model_gateway, "text", fake_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = f"ev-{task_id}"
        data = {"sha256": "snapshot-123", "summary": "source inspected"} if task_id == "inspect" else {"validated": True}
        result = ToolResult(True, "success", proposal.tool, data=data, evidence_id=evidence_id)
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
    plan, graph = runtime.run_goal("inspect then validate", plan_id="plan-context")

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED
    assert graph.tasks["validate"].status == TaskStatus.COMPLETED
    assert len(prompts) == 2
    assert "snapshot-123" in prompts[1]
    assert "source inspected" in prompts[1]



def test_runtime_resume_goal_completed_plan_does_not_reference_missing_context(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"resume regression","tasks":'
                '[{"task_id":"inspect","title":"Inspect workspace","dependencies":[]}],'
                '"acceptance_criteria":[{"type":"task_completed","task_id":"inspect"}]}'
            )
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = "ev-resume-inspect"
        result = ToolResult(True, "success", proposal.tool, data={"ok": True}, evidence_id=evidence_id)
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
    plan, _ = runtime.run_goal("resume regression", plan_id="plan-resume-regression")
    assert plan.status.value == "COMPLETED"

    resumed_plan, resumed_graph = runtime.resume_goal("plan-resume-regression")

    assert resumed_plan.status.value == "COMPLETED"
    assert resumed_graph.tasks["inspect"].status == TaskStatus.COMPLETED


def test_successful_execution_is_not_failed_when_memory_observability_breaks(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"memory isolation","tasks":'
                '[{"task_id":"inspect","title":"Inspect workspace","dependencies":[]}],'
                '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
            )
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = "ev-memory-isolation"
        result = ToolResult(True, "success", proposal.tool, data={"ok": True}, evidence_id=evidence_id)
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
    monkeypatch.setattr(
        runtime.memory,
        "record_experience",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("MEMORY_WRITE_FAILED")),
    )
    monkeypatch.setattr(
        runtime.memory,
        "reflect_and_commit",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("REFLECTION_FAILED")),
    )

    plan, graph = runtime.run_goal("memory isolation", plan_id="plan-memory-isolation")

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED
    assert graph.tasks["inspect"].attempts == 1


def test_automatic_execution_memory_is_bounded_and_ephemeral(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"bounded memory","tasks":'
                '[{"task_id":"inspect","title":"Inspect workspace","dependencies":[]}],'
                '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
            )
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = "ev-memory-bounded"
        result = ToolResult(
            True,
            "success",
            proposal.tool,
            data={"large_output": "x" * 20000},
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

    plan, graph = runtime.run_goal("bounded memory", plan_id="plan-bounded-memory")

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED

    store = runtime.memory.retrieve(
        "execution", task_id="inspect", limit=1
    )
    record = store["results"][0]["record"]
    assert record["retention"] == "ephemeral"
    assert record["value"]["_truncated"] is True
    assert record["value"]["original_chars"] > 20000
    assert len(record["value"]["preview"]) < 7000


def test_memory_prompt_context_bounds_large_values():
    from core.memory_service import memory_prompt_context

    result = {
        "results": [{
            "score": 10,
            "record": {
                "type": "experience",
                "key": "large",
                "value": {"data": "x" * 20000},
                "summary": "large",
                "project_id": None,
                "task_id": None,
                "context": {},
                "source": {"kind": "test", "ref": "ev-1"},
                "provenance": {"reason": "test"},
                "importance": 0.5,
                "confidence": 0.5,
                "retention": "ephemeral",
                "version": 1,
                "updated_at": "2026-01-01T00:00:00Z",
                "tags": [],
            },
        }],
    }

    rows = memory_prompt_context(result)
    assert rows[0]["value"]["_truncated"] is True
    assert len(rows[0]["value"]["preview"]) < 4000


def test_successful_execution_does_not_trigger_reflection_by_default(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    reflection_calls = []

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"reflection budget","tasks":'
                '[{"task_id":"inspect","title":"Inspect","dependencies":[]}],'
                '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
            )
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)
    monkeypatch.setattr(
        runtime.memory,
        "reflect_and_commit",
        lambda **kwargs: reflection_calls.append(kwargs),
    )

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = "ev-reflection-budget"
        result = ToolResult(True, "success", proposal.tool, data={"ok": True}, evidence_id=evidence_id)
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

    plan, _ = runtime.run_goal("reflection budget", plan_id="plan-reflection-budget")

    assert plan.status.value == "COMPLETED"
    assert reflection_calls == []


def test_failed_execution_triggers_reflection(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    reflection_calls = []

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"reflection failure","tasks":'
                '[{"task_id":"inspect","title":"Inspect","dependencies":[]}],'
                '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
            )
        return '{"tool":"lokasi","action":"execute","arguments":{}}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)
    monkeypatch.setattr(
        runtime.memory,
        "reflect_and_commit",
        lambda **kwargs: reflection_calls.append(kwargs) or {
            "status": "success",
            "success": True,
            "committed": [],
            "rejected": [],
        },
    )

    def fake_execute(proposal, *, task_id, attempt_id=None):
        evidence_id = "ev-reflection-failure"
        result = ToolResult(
            False,
            "error",
            proposal.tool,
            data={"reason": "simulated"},
            error="simulated failure",
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

    plan, graph = runtime.run_goal("reflection failure", plan_id="plan-reflection-failure")

    assert plan.status.value == "FAILED"
    assert graph.tasks["inspect"].status == TaskStatus.FAILED
    assert len(reflection_calls) == 1
    assert reflection_calls[0]["evidence_refs"] == ["ev-reflection-failure"]
