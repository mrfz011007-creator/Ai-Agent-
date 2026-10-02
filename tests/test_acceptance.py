from core.contracts import Task, TaskStatus, ToolRequest, VerificationStatus
from core.state_store import StateStore
from core.task_manager import TaskManager
from core.checkpoint import CheckpointManager
from verification.acceptance import AcceptanceCriterion, AcceptanceGate
from verification.artifacts import ArtifactManager
from verification.evidence import EvidenceStore
from verification.verifier import Verifier


def _setup(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    evidence = EvidenceStore(store)
    artifacts = ArtifactManager(store)
    gate = AcceptanceGate(Verifier(evidence), artifacts)
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("A1", "acceptance", status=TaskStatus.READY))
    manager.start(task.task_id)
    return store, evidence, artifacts, gate, manager


def _evidence(evidence, task_id, evidence_id, success=True, attempt_id="attempt-1"):
    from core.contracts import ToolResult
    evidence.record(
        evidence_id=evidence_id,
        task_id=task_id,
        tool="run_command",
        action="build",
        result=ToolResult(success, "SUCCESS" if success else "FAILED", "run_command"),
        attempt_id=attempt_id,
    )


def test_acceptance_requires_artifact(tmp_path):
    _, evidence, _, gate, _ = _setup(tmp_path)
    _evidence(evidence, "A1", "build-1")
    result = gate.verify(task_id="A1", build_evidence_id="build-1")
    assert result.status == VerificationStatus.FAILED
    assert "artifact" in result.reason.lower()


def test_acceptance_requires_valid_artifact_and_tests(tmp_path):
    _, evidence, artifacts, gate, _ = _setup(tmp_path)
    path = tmp_path / "app.apk"
    path.write_bytes(b"apk")
    artifact = artifacts.register(
        task_id="A1",
        path=path,
        kind="APK",
        attempt_id="attempt-1",
        evidence_id="build-1",
    )
    _evidence(evidence, "A1", "build-1")
    _evidence(evidence, "A1", "test-1")
    result = gate.verify(
        task_id="A1",
        build_evidence_id="build-1",
        artifact_ids=(artifact.artifact_id,),
        test_evidence_ids=("test-1",),
        expected_attempt_id="attempt-1",
        criteria=(
            AcceptanceCriterion(
                "apk-present",
                "APK exists and is intact",
                artifact_ids=(artifact.artifact_id,),
            ),
        ),
    )
    assert result.status == VerificationStatus.PASSED
    assert set(result.evidence_ids) == {"build-1", "test-1"}


def test_acceptance_rejects_wrong_attempt(tmp_path):
    _, evidence, artifacts, gate, _ = _setup(tmp_path)
    path = tmp_path / "app.apk"
    path.write_bytes(b"apk")
    artifact = artifacts.register(
        task_id="A1",
        path=path,
        kind="APK",
        attempt_id="attempt-old",
    )
    _evidence(evidence, "A1", "build-1")
    _evidence(evidence, "A1", "test-1")
    result = gate.verify(
        task_id="A1",
        build_evidence_id="build-1",
        artifact_ids=(artifact.artifact_id,),
        test_evidence_ids=("test-1",),
        expected_attempt_id="attempt-current",
    )
    assert result.status == VerificationStatus.FAILED
    assert "attempt" in result.reason.lower()


def test_acceptance_rejects_failed_tests(tmp_path):
    _, evidence, artifacts, gate, _ = _setup(tmp_path)
    path = tmp_path / "app.apk"
    path.write_bytes(b"apk")
    artifact = artifacts.register(task_id="A1", path=path, kind="APK")
    _evidence(evidence, "A1", "build-1")
    _evidence(evidence, "A1", "test-1", success=False)
    result = gate.verify(
        task_id="A1",
        build_evidence_id="build-1",
        artifact_ids=(artifact.artifact_id,),
        test_evidence_ids=("test-1",),
    )
    assert result.status == VerificationStatus.FAILED

def test_acceptance_rejects_artifact_from_different_build(tmp_path):
    _, evidence, artifacts, gate, _ = _setup(tmp_path)
    path = tmp_path / "app.apk"
    path.write_bytes(b"apk")
    artifact = artifacts.register(
        task_id="A1", path=path, kind="APK",
        attempt_id="attempt-1", evidence_id="other-build",
    )
    _evidence(evidence, "A1", "build-1")
    result = gate.verify(
        task_id="A1",
        build_evidence_id="build-1",
        artifact_ids=(artifact.artifact_id,),
        expected_attempt_id="attempt-1",
    )
    assert result.status == VerificationStatus.FAILED
    assert "linked to build evidence" in result.reason


def test_acceptance_criteria_respect_attempt(tmp_path):
    _, evidence, artifacts, gate, _ = _setup(tmp_path)
    path = tmp_path / "app.apk"
    path.write_bytes(b"apk")
    artifact = artifacts.register(
        task_id="A1", path=path, kind="APK",
        attempt_id="attempt-1", evidence_id="build-1",
    )
    _evidence(evidence, "A1", "build-1", attempt_id="attempt-1")
    _evidence(evidence, "A1", "criterion-old", attempt_id="attempt-old")
    result = gate.verify(
        task_id="A1",
        build_evidence_id="build-1",
        artifact_ids=(artifact.artifact_id,),
        expected_attempt_id="attempt-1",
        criteria=(
            AcceptanceCriterion(
                "tests",
                "criterion evidence is current",
                evidence_ids=("criterion-old",),
            ),
        ),
    )
    assert result.status == VerificationStatus.FAILED
    assert "attempt" in result.reason.lower()


def test_completion_requires_acceptance_gate_authority(tmp_path):
    from core.contracts import VerificationResult
    _, _, _, _, manager = _setup(tmp_path)
    manager.begin_verification("A1")
    result = VerificationResult(
        VerificationStatus.PASSED,
        "manually claimed",
        ("build-1",),
    )
    try:
        manager.complete_with_gate("A1", result)
        assert False, "manual verifier result must not complete a gated task"
    except ValueError as exc:
        assert "acceptance-gate" in str(exc)
