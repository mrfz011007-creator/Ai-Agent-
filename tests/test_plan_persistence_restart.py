import tempfile
from pathlib import Path

from core.contracts import Task
from core.orchestrator import Orchestrator
from core.plan import Planner
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

        assert graph2.tasks["roundtrip-001"].title == "Inspect project"
        assert graph2.tasks["roundtrip-002"].title == "Implement change"
        assert graph2.tasks["roundtrip-002"].dependencies == ["roundtrip-001"]

        graph2.validate()
