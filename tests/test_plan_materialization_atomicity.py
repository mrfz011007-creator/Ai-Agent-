import pytest

from core.state_store import StateStore


def test_plan_and_initial_tasks_rollback_together_on_insert_failure(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")

    with pytest.raises(Exception):
        store.save_plan_with_tasks(
            plan_id="P-ATOMIC",
            goal="atomic materialization",
            status="VALIDATED",
            task_ids=("T1", "T1"),
            acceptance_criteria=("complete",),
            tasks=[
                ("T1", "PENDING", 0, {"title": "first"}),
                ("T1", "PENDING", 0, {"title": "duplicate"}),
            ],
        )

    assert store.load_task("T1") is None
    assert store.load_plan("P-ATOMIC") is None


def test_orchestrator_rejects_existing_persisted_task(tmp_path):
    from core.orchestrator import Orchestrator
    from core.plan import Planner, PlanProposal
    from core.contracts import Task
    from core.task_manager import TaskManager

    store = StateStore(tmp_path / "state.sqlite3")
    store.save_task("T1", "PENDING", 0, {"title": "existing"})

    manager = TaskManager(store=store)
    orchestrator = Orchestrator(manager, Planner(), store=store)

    proposal = PlanProposal(
        goal="duplicate task",
        tasks=(Task("T1", "new"),),
        acceptance_criteria=("done",),
    )

    with pytest.raises(ValueError, match="Persisted task already exists"):
        orchestrator.materialize(proposal, "P-DUP")
