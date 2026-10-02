from core.state_store import StateStore
from core.task_manager import TaskManager
from core.checkpoint import CheckpointManager
from core.orchestrator import Orchestrator
from core.plan import Planner, PlanProposal
from core.contracts import Task, TaskStatus


def test_plan_and_task_graph_survive_restart(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = StateStore(db)
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    orchestrator = Orchestrator(manager, Planner(), store=store)

    proposal = PlanProposal(
        goal="restart-safe goal",
        tasks=(
            Task("one", "First"),
            Task("two", "Second", dependencies=["one"]),
        ),
        acceptance_criteria=("both complete",),
    )
    plan, graph = orchestrator.materialize(proposal, "plan-restart")
    manager.start("one")
    manager.fail("one", "simulated interruption")

    restarted_store = StateStore(db)
    restarted_manager = TaskManager(
        store=restarted_store,
        checkpoints=CheckpointManager(restarted_store),
    )
    restored = Orchestrator(restarted_manager, Planner(), store=restarted_store).restore_graph("plan-restart")

    assert restored is not None
    restored_plan, restored_graph = restored
    assert restored_plan.plan_id == plan.plan_id
    assert restored_graph.tasks["one"].status == TaskStatus.FAILED
    assert restored_graph.tasks["two"].dependencies == ["one"]


def test_latest_checkpoint_is_available_after_restart(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = StateStore(db)
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("checkpoint", "Checkpoint me"))
    manager.start(task.task_id)

    restarted = StateStore(db)
    checkpoint = restarted.load_latest_checkpoint("checkpoint")

    assert checkpoint is not None
    assert checkpoint["task_id"] == "checkpoint"
    assert checkpoint["payload"]["status"] == "RUNNING"
