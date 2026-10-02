from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.contracts import BuildResult, TestResult, ToolRequest
from execution.router import ToolRouter
from verification.artifacts import ArtifactManager


@dataclass(frozen=True)
class CommandResult:
    success: bool
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    evidence_id: str | None = None


class BuildManager:
    """Builds through the shared execution boundary and registers artifacts."""

    def __init__(self, artifact_manager: ArtifactManager, router: ToolRouter | None = None):
        self.artifact_manager = artifact_manager
        self.router = router

    def build(
        self,
        *,
        task_id: str,
        command: str,
        cwd: str | Path,
        artifact_paths: list[str | Path] | None = None,
        attempt_id: str | None = None,
        source_commit: str | None = None,
        timeout: float = 900.0,
    ) -> BuildResult:
        result = self._run(command, cwd, timeout, task_id, attempt_id)
        if not result.success:
            return BuildResult(
                False, command, result.exit_code, evidence_id=result.evidence_id,
                error=result.stderr or result.stdout
            )

        artifact_ids: list[str] = []
        build_root = Path(cwd).resolve()
        for path in artifact_paths or []:
            artifact_path = (build_root / path).resolve()
            try:
                artifact_path.relative_to(build_root)
            except ValueError as error:
                raise ValueError("Artifact path must remain inside build workspace") from error
            artifact = self.artifact_manager.register(
                task_id=task_id,
                path=artifact_path,
                kind=self._kind_for(path),
                attempt_id=attempt_id,
                source_commit=source_commit,
                evidence_id=result.evidence_id,
            )
            artifact_ids.append(artifact.artifact_id)

        return BuildResult(
            True, command, result.exit_code, tuple(artifact_ids),
            evidence_id=result.evidence_id,
        )

    def _run(
        self, command: str, cwd: str | Path, timeout: float, task_id: str | None = None,
        attempt_id: str | None = None,
    ) -> CommandResult:
        if self.router is None:
            raise RuntimeError("BuildManager requires ToolRouter for controlled execution")
        result = self.router.execute(
            ToolRequest(
                tool="run_command",
                action="build",
                arguments={"command": command, "cwd": str(cwd), "timeout": timeout},
                source="build_manager",
                task_id=task_id,
                attempt_id=attempt_id,
            )
        )
        data = result.data if isinstance(result.data, dict) else {}
        return CommandResult(
            result.success,
            command,
            data.get("exit_code"),
            data.get("stdout", ""),
            data.get("stderr", result.error or ""),
            result.evidence_id,
        )

    @staticmethod
    def _kind_for(path: str | Path) -> str:
        suffix = Path(path).suffix.lower()
        return {".apk": "APK", ".aab": "AAB"}.get(suffix, "BUILD_ARTIFACT")


class TestManager:
    """Runs tests through the shared execution boundary."""

    def __init__(self, router: ToolRouter | None = None):
        self.router = router

    def run(
        self,
        *,
        command: str,
        cwd: str | Path,
        timeout: float = 900.0,
        task_id: str | None = None,
        attempt_id: str | None = None,
    ) -> TestResult:
        if self.router is None:
            raise RuntimeError("TestManager requires ToolRouter for controlled execution")
        result = self.router.execute(
            ToolRequest(
                tool="run_command",
                action="test",
                arguments={"command": command, "cwd": str(cwd), "timeout": timeout},
                source="test_manager",
                task_id=task_id,
                attempt_id=attempt_id,
            )
        )
        data = result.data if isinstance(result.data, dict) else {}
        return TestResult(
            result.success,
            command,
            data.get("exit_code"),
            result.evidence_id,
            None if result.success else (data.get("stderr") or result.error or data.get("stdout")),
        )
