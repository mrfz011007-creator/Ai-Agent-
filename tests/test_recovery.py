from core.contracts import Task, TaskStatus
from core.task_manager import TaskManager
from core.state_store import StateStore
from core.checkpoint import CheckpointManager
from core.recovery import RecoveryManager, ReconcileOutcome
from core.contracts import ToolResult
from verification.evidence import EvidenceStore


def test_unknown_state_is_blocked(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R1", "reconcile"))
    task.status = TaskStatus.READY
    manager.start("R1")

    recovery = RecoveryManager(manager)
    recovery.recover_task("R1")

    result = recovery.reconcile("R1", ReconcileOutcome.UNKNOWN, "State is unknown.")
    assert result.status == TaskStatus.BLOCKED
    assert result.action == "BLOCK"


def test_safe_resume_requires_explicit_reason(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R2", "resume"))
    task.status = TaskStatus.READY
    manager.start("R2")

    recovery = RecoveryManager(manager, EvidenceStore(store))
    recovery.recover_task("R2")

    evidence = EvidenceStore(store)
    evidence.record(
        evidence_id="E-R2",
        task_id="R2",
        tool="reconcile",
        action="inspect",
        result=ToolResult(True, "SUCCESS", "reconcile"),
    )
    result = recovery.reconcile(
        "R2",
        ReconcileOutcome.SAFE_TO_RESUME,
        "Workspace state matches checkpoint.",
        evidence_ids=("E-R2",),
    )
    assert result.status == TaskStatus.RUNNING
    assert result.action == "RESUME"


def test_safe_resume_requires_evidence(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R3", "resume"))
    task.status = TaskStatus.READY
    manager.start("R3")

    evidence = EvidenceStore(store)
    recovery = RecoveryManager(manager, evidence)
    recovery.recover_task("R3")

    try:
        recovery.reconcile(
            "R3",
            ReconcileOutcome.SAFE_TO_RESUME,
            "State reconciled.",
        )
        assert False, "safe resume should require evidence"
    except ValueError as exc:
        assert "evidence" in str(exc).lower()


def test_evidence_survives_restart_and_allows_safe_resume(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    store = StateStore(db_path)
    evidence = EvidenceStore(store)
    evidence.record(
        evidence_id="E-R4",
        task_id="R4",
        tool="reconcile",
        action="inspect",
        result=ToolResult(True, "SUCCESS", "reconcile"),
    )

    restarted_store = StateStore(db_path)
    restarted_evidence = EvidenceStore(restarted_store)
    assert restarted_evidence.get("E-R4") is not None

    manager = TaskManager(
        store=restarted_store,
        checkpoints=CheckpointManager(restarted_store),
    )
    task = manager.create(Task("R4", "resume"))
    task.status = TaskStatus.READY
    manager.start("R4")

    recovery = RecoveryManager(manager, restarted_evidence)
    recovery.recover_task("R4")
    result = recovery.reconcile(
        "R4",
        ReconcileOutcome.SAFE_TO_RESUME,
        "State reconciled.",
        evidence_ids=("E-R4",),
    )
    assert result.status == TaskStatus.RUNNING


def test_invalid_evidence_cannot_resume(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R5", "resume"))
    task.status = TaskStatus.READY
    manager.start("R5")
    recovery = RecoveryManager(manager, EvidenceStore(store))
    recovery.recover_task("R5")

    try:
        recovery.reconcile(
            "R5",
            ReconcileOutcome.SAFE_TO_RETRY,
            "State reconciled.",
            evidence_ids=("missing",),
        )
        assert False, "invalid evidence should not authorize retry"
    except ValueError as exc:
        assert "evidence" in str(exc).lower()
