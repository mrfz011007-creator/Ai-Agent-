from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from core.contracts import Budget


@dataclass
class BudgetManager:
    budget: Budget
    started_at: float = 0.0
    state_store: Any | None = None
    wall_clock: Any = time.time
    started_at_wall: float = 0.0

    def __post_init__(self) -> None:
        if self.state_store is not None:
            saved = self.state_store.load_budget()
            if saved is not None:
                for field in (
                    "max_tool_calls", "max_model_calls", "max_recovery_cycles",
                    "max_runtime_seconds", "max_output_chars",
                    "tool_calls", "model_calls", "recovery_cycles",
                ):
                    if field in saved:
                        setattr(self.budget, field, saved[field])
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

    def reserve_model_call(self) -> None:
        self.check_runtime()
        previous = self.budget.model_calls
        self.budget.consume_model()
        try:
            self._persist()
        except Exception:
            self.budget.model_calls = previous
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

    def snapshot(self) -> dict[str, int | float]:
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
        }
