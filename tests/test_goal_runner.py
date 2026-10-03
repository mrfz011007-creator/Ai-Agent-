from core.contracts import TaskStatus, ToolResult
from core.runtime import AgentRuntime


def test_runtime_run_goal_completes_through_execution_and_acceptance(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    def fake_generate_text(prompt, **kwargs):
        if "USER GOAL:" in prompt:
            return (
                '{"goal":"inspect workspace","tasks":'
                '[{"task_id":"inspect","title":"Inspect workspace","dependencies":[]}],'
                '"acceptance_criteria":["inspection succeeds"]}'
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
                '"acceptance_criteria":[]}'
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
            '"acceptance_criteria":["both steps succeed"]}'
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

def test_runtime_resume_goal_does_not_implicitly_retry_waiting_task(tmp_path, monkeypatch):
    from core.contracts import Task
    from core.plan import Planner

    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    proposal = Planner().propose(
        "recover safely", [Task("interrupted", "Interrupted work")]
    )
    plan, _ = runtime.orchestrator.materialize(proposal, "plan-recovery")
    runtime.task_manager.start("interrupted")

    calls = []
    monkeypatch.setattr(
        runtime.model_gateway,
        "text",
        lambda prompt, **kwargs: calls.append(prompt) or '{"tool":"lokasi","action":"execute","arguments":{}}',
    )

    fresh = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    resumed_plan, graph = fresh.resume_goal(plan.plan_id)

    assert resumed_plan.status.value == "WAITING"
    assert graph.tasks["interrupted"].status == TaskStatus.WAITING
    assert graph.tasks["interrupted"].attempts == 1
    assert calls == []
