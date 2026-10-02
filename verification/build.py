from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.contracts import BuildResult, TestResult
from verification.artifacts import ArtifactManager


@dataclass(frozen=True)
class CommandResult:
    success: bool
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""


class BuildManager:
    """Runs bounded project builds and registers declared output artifacts."""

    def __init__(self, artifact_manager: ArtifactManager):
        self.artifact_manager = artifact_manager

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
        result = self._run(command, cwd, timeout)
        if not result.success:
            return BuildResult(
                False, command, result.exit_code, error=result.stderr or result.stdout
            )

        artifact_ids: list[str] = []
        for path in artifact_paths or []:
            artifact = self.artifact_manager.register(
                task_id=task_id,
                path=Path(cwd) / path,
                kind=self._kind_for(path),
                attempt_id=attempt_id,
                source_commit=source_commit,
            )
            artifact_ids.append(artifact.artifact_id)

        return BuildResult(
            True, command, result.exit_code, tuple(artifact_ids)
        )

    @staticmethod
    def _run(command: str, cwd: str | Path, timeout: float) -> CommandResult:
        try:
            completed = subprocess.run(
                shlex.split(command),
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return CommandResult(False, command, None, error.stdout or "", error.stderr or "TIMEOUT")
        except OSError as error:
            return CommandResult(False, command, None, "", str(error))

        return CommandResult(
            completed.returncode == 0,
            command,
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )

    @staticmethod
    def _kind_for(path: str | Path) -> str:
        suffix = Path(path).suffix.lower()
        return {".apk": "APK", ".aab": "AAB"}.get(suffix, "BUILD_ARTIFACT")


class TestManager:
    """Runs bounded test commands; test output becomes caller-owned evidence."""

    def run(
        self,
        *,
        command: str,
        cwd: str | Path,
        timeout: float = 900.0,
    ) -> TestResult:
        result = BuildManager._run(command, cwd, timeout)
        return TestResult(
            result.success,
            command,
            result.exit_code,
            None if result.success else (result.stderr or result.stdout),
        )
