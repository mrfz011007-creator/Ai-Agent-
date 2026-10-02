from core.contracts import ToolResult, VerificationStatus
from core.state_store import StateStore
from verification.artifacts import ArtifactManager
from verification.build import TestManager as ArtifactTestManager
from verification.evidence import EvidenceStore
from verification.verifier import Verifier


def test_artifact_registration_and_integrity(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    artifact_path = tmp_path / "app-debug.apk"
    artifact_path.write_bytes(b"apk-test")

    manager = ArtifactManager(store)
    artifact = manager.register(
        task_id="A1",
        attempt_id="attempt-1",
        path=artifact_path,
        kind="APK",
        source_commit="abc123",
    )

    assert artifact.task_id == "A1"
    assert manager.verify(artifact.artifact_id, "A1") == (True, "ARTIFACT_VALID")
    assert manager.verify(artifact.artifact_id, "A2") == (
        False,
        "ARTIFACT_TASK_MISMATCH",
    )


def test_artifact_integrity_detects_modified_file(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    artifact_path = tmp_path / "app-debug.apk"
    artifact_path.write_bytes(b"apk-test")

    manager = ArtifactManager(store)
    artifact = manager.register(task_id="A2", path=artifact_path, kind="APK")
    artifact_path.write_bytes(b"modified")

    assert manager.verify(artifact.artifact_id, "A2") == (
        False,
        "ARTIFACT_HASH_CHANGED",
    )


def test_verifier_validates_all_task_evidence():
    evidence = EvidenceStore()
    evidence.record(
        evidence_id="E-A1",
        task_id="A1",
        tool="build",
        action="assemble",
        result=ToolResult(True, "SUCCESS", "build"),
    )
    verifier = Verifier(evidence)

    result = verifier.verify_task_evidence("A1", ("E-A1",))
    assert result.status == VerificationStatus.PASSED

    wrong_task = verifier.verify_task_evidence("A2", ("E-A1",))
    assert wrong_task.status == VerificationStatus.FAILED


from verification.build import BuildManager, TestManager

class _FakeRouter:
    def __init__(self, success=True):
        self.success = success

    def execute(self, request):
        if self.success:
            from pathlib import Path
            command = request.arguments["command"]
            if "app-debug.apk" in command:
                Path(request.arguments["cwd"], "app-debug.apk").write_bytes(b"apk")
            return ToolResult(
                True, "success", request.tool,
                data={"success": True, "status": "SUCCESS", "exit_code": 0,
                      "stdout": "ok", "stderr": ""},
                evidence_id="E-BUILD",
            )
        return ToolResult(
            False, "error", request.tool,
            data={"success": False, "status": "FAILED", "exit_code": 1,
                  "stdout": "", "stderr": "failed"},
            error="failed",
            evidence_id="E-FAIL",
        )


def test_build_manager_registers_artifact(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    output = tmp_path / "app-debug.apk"
    output.write_bytes(b"apk")
    manager = BuildManager(ArtifactManager(store), _FakeRouter())

    result = manager.build(
        task_id="B1",
        command="python -c \"from pathlib import Path; Path('app-debug.apk').write_bytes(b'apk')\"",
        cwd=tmp_path,
        artifact_paths=["app-debug.apk"],
        attempt_id="attempt-1",
    )

    assert result.success
    assert len(result.artifact_ids) == 1


def test_test_manager_reports_exit_status(tmp_path):
    result = ArtifactTestManager(_FakeRouter()).run(
        command="python -c \"print('ok')\"",
        cwd=tmp_path,
    )
    assert result.success
    assert result.exit_code == 0
