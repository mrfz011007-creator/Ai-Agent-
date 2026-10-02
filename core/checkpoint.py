from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import uuid
from typing import Any

from core.contracts import Task
from core.state_store import StateStore


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    task_id: str | None
    status: str
    attempt: int
    payload: dict[str, Any]
    created_at: str


class CheckpointManager:
    def __init__(self, store: StateStore):
        self.store = store

    def capture(self, task: Task, **payload: Any) -> Checkpoint:
        checkpoint = Checkpoint(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:12]}",
            task_id=task.task_id,
            status=task.status.value,
            attempt=task.attempts,
            payload=payload,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.save_checkpoint(
            checkpoint.checkpoint_id,
            checkpoint.task_id,
            asdict(checkpoint),
        )
        return checkpoint

    def latest(self, task_id: str | None = None) -> dict[str, Any] | None:
        return self.store.load_latest_checkpoint(task_id)
