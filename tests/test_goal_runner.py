from core.contracts import TaskStatus
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

    plan, graph = runtime.run_goal("inspect workspace", plan_id="plan-run")

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED
    assert runtime.state_store.load_plan("plan-run")["status"] == "COMPLETED"
    evidence = runtime.state_store.load_evidence_for_task("inspect")
    assert evidence
    assert all(item["success"] for item in evidence)


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

    plan, graph = runtime.run_goal("bounded", plan_id="plan-bound", max_steps=1)

    assert plan.status.value == "WAITING"
    assert graph.tasks["one"].status == TaskStatus.COMPLETED
    assert graph.tasks["two"].status == TaskStatus.PENDING
    assert runtime.state_store.load_plan("plan-bound")["status"] == "WAITING"


def test_runtime_resume_goal_is_plan_scoped(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    plan = runtime.plan_goal(
        "plan one",
        plan_id="plan-one",
    )
    runtime.task_manager.start("build")

    other = runtime.plan_goal(
        "plan two",
        plan_id="plan-two",
    )
    runtime.task_manager.start("build")

    # Restore one plan in a fresh runtime to prove recovery only touches its tasks.
    fresh = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    restored = fresh.orchestrator.restore_graph(plan[0].plan_id)
    assert restored is not None
    fresh.runtime_model_for_test = None

    # The second plan remains interrupted until explicitly resumed.
    second = fresh.orchestrator.restore_graph(other[0].plan_id)
    assert second[1].tasks["build"].status == TaskStatus.RUNNING
