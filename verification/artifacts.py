from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.contracts import Artifact
from core.state_store import StateStore


class ArtifactManager:
    """Registers immutable artifact metadata and verifies file integrity."""

    def __init__(self, store: StateStore):
        self.store = store

    def register(self, *, task_id: str, path: str | Path, kind: str,
                 attempt_id: str | None = None,
                 source_commit: str | None = None,
                 evidence_id: str | None = None) -> Artifact:
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Artifact not found: {file_path}")

        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

        artifact = Artifact(
            artifact_id=f"artifact-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            attempt_id=attempt_id,
            path=str(file_path.resolve()),
            kind=kind,
            sha256=digest.hexdigest(),
            size=file_path.stat().st_size,
            source_commit=source_commit,
            evidence_id=evidence_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.save_artifact(asdict(artifact))
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        row = self.store.load_artifact(artifact_id)
        if row is None:
            return None
        return Artifact(
            artifact_id=row["artifact_id"],
            task_id=row["task_id"],
            attempt_id=row["attempt_id"],
            path=row["path"],
            kind=row["kind"],
            sha256=row["sha256"],
            size=int(row["size"]),
            source_commit=row["source_commit"],
            evidence_id=row.get("evidence_id"),
            created_at=row["payload"].get("created_at") or row["created_at"],
        )

    def verify(self, artifact_id: str, task_id: str | None = None) -> tuple[bool, str]:
        artifact = self.get(artifact_id)
        if artifact is None:
            return False, "ARTIFACT_NOT_FOUND"
        if task_id is not None and artifact.task_id != task_id:
            return False, "ARTIFACT_TASK_MISMATCH"

        path = Path(artifact.path)
        if not path.is_file():
            return False, "ARTIFACT_MISSING"
        if path.stat().st_size != artifact.size:
            return False, "ARTIFACT_SIZE_CHANGED"

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

        if digest.hexdigest() != artifact.sha256:
            return False, "ARTIFACT_HASH_CHANGED"
        return True, "ARTIFACT_VALID"
