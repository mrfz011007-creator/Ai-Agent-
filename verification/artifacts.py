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
                 source_commit: str | None = None) -> Artifact:
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
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.save_artifact(asdict(artifact))
        return artifact
