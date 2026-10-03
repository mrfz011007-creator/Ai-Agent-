import tempfile
from pathlib import Path

from core.contracts import Task
from core.orchestrator import Orchestrator
from core.plan import Planner, PlanStatus
from core.runtime import AgentRuntime


def test_materialized_plan_and_graph_round_trip_after_runtime_restart():
    with tempfile.TemporaryDirectory(prefix="ai-agent-plan-roundtrip-") as tmp:
        state_path = Path(tmp) / "state.sqlite3"

        runtime1 = AgentRuntime.create(state_path=state_path)
        orchestrator1 = Orchestrator(
            runtime1.task_manager,
            Planner(),
            store=runtime1.state_store,
        )

        proposal = orchestrator1.planner.propose(
            "E2E plan persistence",
            [
                Task("roundtrip-001", "Inspect project"),
                Task(
                    "roundtrip-002",
                    "Implement change",
                    dependencies=["roundtrip-001"],
                ),
            ],
            acceptance_criteria=("Both tasks survive restart",),
        )

        plan1, graph1 = orchestrator1.materialize(
            proposal,
            "roundtrip-plan-001",
            project_id="launcher",
        )

        assert plan1.status.value == "VALIDATED"
        assert tuple(graph1.tasks) == (
            "roundtrip-001",
            "roundtrip-002",
        )

        runtime2 = AgentRuntime.create(state_path=state_path)
        orchestrator2 = Orchestrator(
            runtime2.task_manager,
            Planner(),
            store=runtime2.state_store,
        )

        restored = orchestrator2.restore_graph("roundtrip-plan-001")

        assert restored is not None
        plan2, graph2 = restored

        assert plan2.plan_id == plan1.plan_id
        assert plan2.goal == plan1.goal
        assert plan2.status == plan1.status
        assert plan2.task_ids == plan1.task_ids
        assert plan2.acceptance_criteria == plan1.acceptance_criteria
        assert plan2.project_id == "launcher"


        assert graph2.tasks["roundtrip-001"].title == "Inspect project"
        assert graph2.tasks["roundtrip-002"].title == "Implement change"
        assert graph2.tasks["roundtrip-002"].dependencies == ["roundtrip-001"]

        graph2.validate()


def test_resume_goal_restores_persisted_project_scope_without_repassing_it(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    state_path = tmp_path / "state.sqlite3"
    runtime1 = AgentRuntime.create(state_path=state_path)
    proposal = runtime1.orchestrator.planner.propose(
        "Scoped resume",
        [Task("scoped-001", "Inspect launcher")],
        acceptance_criteria=("Scope survives restart",),
    )
    runtime1.orchestrator.materialize(
        proposal,
        "scoped-plan-001",
        project_id="launcher",
    )

    runtime2 = AgentRuntime.create(state_path=state_path)
    resumed_plan, graph = runtime2.resume_goal(
        "scoped-plan-001",
        max_steps=0,
    )

    assert resumed_plan.project_id == "launcher"
    assert graph.tasks["scoped-001"].status.value == "PENDING"
    assert resumed_plan.status.value == "WAITING"


def test_existing_plan_table_migrates_without_project_scope(monkeypatch, tmp_path):
    import json
    import sqlite3

    state_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(state_path) as db:
        db.execute(
            """
            CREATE TABLE plans (
                plan_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                status TEXT NOT NULL,
                task_ids TEXT NOT NULL,
                acceptance_criteria TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        db.execute(
            """
            INSERT INTO plans(
                plan_id, goal, status, task_ids, acceptance_criteria
            ) VALUES (?,?,?,?,?)
            """,
            (
                "legacy-plan",
                "Legacy plan",
                "VALIDATED",
                json.dumps(["legacy-task"]),
                json.dumps(["legacy criteria"]),
            ),
        )

    runtime = AgentRuntime.create(state_path=state_path)
    restored = runtime.orchestrator.restore_plan("legacy-plan")

    assert restored is not None
    assert restored.project_id is None
    assert restored.plan_id == "legacy-plan"
    assert restored.goal == "Legacy plan"


def test_plan_project_scope_survives_status_updates_and_restart():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="ai-agent-plan-scope-") as tmp:
        state_path = Path(tmp) / "state.sqlite3"
        runtime1 = AgentRuntime.create(state_path=state_path)
        proposal = runtime1.orchestrator.planner.propose(
            "Scoped status update",
            [Task("scope-status-001", "Inspect project")],
            acceptance_criteria=("Scope must survive status updates",),
        )
        plan, _ = runtime1.orchestrator.materialize(
            proposal,
            "scope-status-plan",
            project_id="launcher",
        )

        runtime1.orchestrator.persist_plan(
            plan.__class__(
                plan_id=plan.plan_id,
                goal=plan.goal,
                task_ids=plan.task_ids,
                status=PlanStatus.EXECUTING,
                acceptance_criteria=plan.acceptance_criteria,
                project_id=plan.project_id,
            )
        )

        runtime2 = AgentRuntime.create(state_path=state_path)
        restored = runtime2.orchestrator.restore_plan("scope-status-plan")
        assert restored is not None
        assert restored.project_id == "launcher"
