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

def test_acceptance_criteria_reject_artifact_from_wrong_attempt(tmp_path):
    _, evidence, artifacts, gate, _ = _setup(tmp_path)
    path = tmp_path / "app.apk"
    path.write_bytes(b"apk")
    artifact = artifacts.register(
        task_id="A1",
        path=path,
        kind="APK",
        attempt_id="attempt-old",
        evidence_id="build-1",
    )
    _evidence(evidence, "A1", "build-1", attempt_id="attempt-current")
    result = gate.verify(
        task_id="A1",
        build_evidence_id="build-1",
        artifact_ids=(artifact.artifact_id,),
        expected_attempt_id="attempt-current",
        criteria=(
            AcceptanceCriterion(
                "apk",
                "APK belongs to the current attempt",
                artifact_ids=(artifact.artifact_id,),
            ),
        ),
    )
    assert result.status == VerificationStatus.FAILED
    assert "another attempt" in result.reason



def test_acceptance_proves_build_test_artifact_chain_through_router(tmp_path):
    from core.budget import Budget, BudgetManager
    from execution.command import run_command
    from execution.router import ToolRouter
    from security.policy import PolicyEngine
    from verification.build import BuildManager, TestManager

    store = StateStore(tmp_path / "state.sqlite3")
    evidence = EvidenceStore(store)
    artifacts = ArtifactManager(store)
    gate = AcceptanceGate(Verifier(evidence), artifacts)

    registry = {
        "run_command": {
            "func": run_command,
            "permission": "safe",
            "idempotent": True,
        }
    }
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=evidence,
    )
    build = BuildManager(artifacts, router)
    tests = TestManager(router)
    task_id = "CHAIN-1"
    attempt_id = "CHAIN-1:attempt:1"

    build_result = build.build(
        task_id=task_id,
        attempt_id=attempt_id,
        command="python -c \"from pathlib import Path; Path('app-debug.apk').write_bytes(b'proof-apk')\"",
        cwd=tmp_path,
        artifact_paths=["app-debug.apk"],
        source_commit="test-commit",
    )
    assert build_result.success is True
    assert build_result.evidence_id is not None
    assert len(build_result.artifact_ids) == 1

    test_result = tests.run(
        task_id=task_id,
        attempt_id=attempt_id,
        command="python -c \"from pathlib import Path; assert Path('app-debug.apk').read_bytes() == b'proof-apk'\"",
        cwd=tmp_path,
    )
    assert test_result.success is True
    assert test_result.evidence_id is not None

    result = gate.verify(
        task_id=task_id,
        build_evidence_id=build_result.evidence_id,
        artifact_ids=build_result.artifact_ids,
        test_evidence_ids=(test_result.evidence_id,),
        expected_attempt_id=attempt_id,
    )
    assert result.status == VerificationStatus.PASSED
    assert result.authority == "acceptance_gate"
    assert set(result.evidence_ids) == {
        build_result.evidence_id,
        test_result.evidence_id,
    }

    artifact = artifacts.get(build_result.artifact_ids[0])
    assert artifact is not None
    assert artifact.evidence_id == build_result.evidence_id
    assert artifact.attempt_id == attempt_id

    (tmp_path / "app-debug.apk").write_bytes(b"tampered")
    tampered = gate.verify(
        task_id=task_id,
        build_evidence_id=build_result.evidence_id,
        artifact_ids=build_result.artifact_ids,
        test_evidence_ids=(test_result.evidence_id,),
        expected_attempt_id=attempt_id,
    )
    assert tampered.status == VerificationStatus.FAILED
    assert "integrity" in tampered.reason.lower()
