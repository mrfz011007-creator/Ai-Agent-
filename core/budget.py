from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid
from typing import Any

from core.contracts import Budget


@dataclass
class BudgetManager:
    budget: Budget
    started_at: float = 0.0
    state_store: Any | None = None
    wall_clock: Any = time.time
    started_at_wall: float = 0.0
    model_call_events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state_store is not None:
            saved = self.state_store.load_budget() or {}
            if saved is not None:
                for field in (
                    "max_tool_calls", "max_model_calls", "max_recovery_cycles",
                    "max_runtime_seconds", "max_output_chars",
                    "tool_calls", "model_calls", "recovery_cycles",
                ):
                    if field in saved:
                        setattr(self.budget, field, saved[field])
            saved_events = saved.get("model_call_events", [])
            if isinstance(saved_events, list):
                self.model_call_events = [
                    dict(event) for event in saved_events if isinstance(event, dict)
                ][-200:]
            # Runtime duration is a per-process execution bound. Counters remain
            # persistent, but a restart must receive a fresh runtime window.
            self.started_at_wall = self.wall_clock()
            self._persist()
        if self.started_at == 0.0:
            self.started_at = time.monotonic()

    def _persist(self) -> None:
        if self.state_store is None:
            return
        self.state_store.save_budget({
            "max_tool_calls": self.budget.max_tool_calls,
            "max_model_calls": self.budget.max_model_calls,
            "max_recovery_cycles": self.budget.max_recovery_cycles,
            "max_runtime_seconds": self.budget.max_runtime_seconds,
            "max_output_chars": self.budget.max_output_chars,
            "tool_calls": self.budget.tool_calls,
            "model_calls": self.budget.model_calls,
            "recovery_cycles": self.budget.recovery_cycles,
            "started_at_wall": self.started_at_wall,
            "model_call_events": self.model_call_events[-200:],
        })

    def check_runtime(self) -> None:
        if self.elapsed_seconds >= self.budget.max_runtime_seconds:
            raise RuntimeError("RUNTIME_BUDGET_EXCEEDED")

    def reserve_tool_call(self) -> None:
        self.check_runtime()
        previous = self.budget.tool_calls
        self.budget.consume_tool()
        try:
            self._persist()
        except Exception:
            self.budget.tool_calls = previous
            raise

    def reserve_model_call(
        self,
        *,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        credential: str | None = None,
    ) -> str:
        """Reserve one actual provider attempt and persist its audit record."""
        self.check_runtime()
        previous = self.budget.model_calls
        event = {
            "event_id": uuid.uuid4().hex,
            "request_id": request_id or uuid.uuid4().hex,
            "provider": provider or "unknown",
            "model": model or "unknown",
            "credential": credential or "unknown",
            "status": "STARTED",
            "started_at": round(self.wall_clock(), 3),
            "duration_ms": None,
            "error_kind": None,
        }
        self.budget.consume_model()
        self.model_call_events.append(event)
        self.model_call_events = self.model_call_events[-200:]
        try:
            self._persist()
        except Exception:
            self.budget.model_calls = previous
            self.model_call_events = [
                item for item in self.model_call_events
                if item.get("event_id") != event["event_id"]
            ]
            raise
        return event["event_id"]

    def finish_model_call(
        self,
        event_id: str,
        *,
        status: str,
        duration_ms: int | None = None,
        error_kind: str | None = None,
    ) -> None:
        """Close an attempt without persisting raw provider errors or credentials."""
        if status not in {"SUCCEEDED", "FAILED", "SKIPPED"}:
            raise ValueError("MODEL_CALL_STATUS_INVALID")
        event = next(
            (item for item in self.model_call_events if item.get("event_id") == event_id),
            None,
        )
        if event is None:
            raise KeyError("MODEL_CALL_EVENT_NOT_FOUND")
        previous = dict(event)
        event.update({
            "status": status,
            "duration_ms": max(0, int(duration_ms)) if duration_ms is not None else None,
            "error_kind": error_kind,
            "finished_at": round(self.wall_clock(), 3),
        })
        try:
            self._persist()
        except Exception:
            event.clear()
            event.update(previous)
            raise

    def reserve_recovery_cycle(self) -> None:
        self.check_runtime()
        previous = self.budget.recovery_cycles
        self.budget.consume_recovery()
        try:
            self._persist()
        except Exception:
            self.budget.recovery_cycles = previous
            raise

    def release_recovery_cycle(self) -> None:
        """Undo a recovery reservation when the following state transition fails."""
        if self.budget.recovery_cycles <= 0:
            raise ValueError("No recovery cycle is reserved")
        previous = self.budget.recovery_cycles
        self.budget.recovery_cycles -= 1
        try:
            self._persist()
        except Exception:
            self.budget.recovery_cycles = previous
            raise

    @property
    def elapsed_seconds(self) -> float:
        if self.state_store is not None:
            return max(0.0, self.wall_clock() - self.started_at_wall)
        return max(0.0, time.monotonic() - self.started_at)

    @property
    def remaining_tool_calls(self) -> int:
        return max(0, self.budget.max_tool_calls - self.budget.tool_calls)

    @property
    def remaining_model_calls(self) -> int:
        return max(0, self.budget.max_model_calls - self.budget.model_calls)

    @property
    def remaining_recovery_cycles(self) -> int:
        return max(0, self.budget.max_recovery_cycles - self.budget.recovery_cycles)

    @property
    def remaining_runtime_seconds(self) -> float:
        return max(0.0, self.budget.max_runtime_seconds - self.elapsed_seconds)

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_tool_calls": self.budget.max_tool_calls,
            "tool_calls": self.budget.tool_calls,
            "remaining_tool_calls": self.remaining_tool_calls,
            "max_model_calls": self.budget.max_model_calls,
            "model_calls": self.budget.model_calls,
            "remaining_model_calls": self.remaining_model_calls,
            "max_recovery_cycles": self.budget.max_recovery_cycles,
            "recovery_cycles": self.budget.recovery_cycles,
            "remaining_recovery_cycles": self.remaining_recovery_cycles,
            "max_runtime_seconds": self.budget.max_runtime_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "remaining_runtime_seconds": self.remaining_runtime_seconds,
            "max_output_chars": self.budget.max_output_chars,
            "recent_model_call_events": [dict(event) for event in self.model_call_events[-20:]],
        }
