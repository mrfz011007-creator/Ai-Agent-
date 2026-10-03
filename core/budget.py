from __future__ import annotations

from dataclasses import dataclass
import time

from core.contracts import Budget


@dataclass
class BudgetManager:
    budget: Budget
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if self.started_at == 0.0:
            self.started_at = time.monotonic()

    def check_runtime(self) -> None:
        if self.elapsed_seconds >= self.budget.max_runtime_seconds:
            raise RuntimeError("RUNTIME_BUDGET_EXCEEDED")

    def reserve_tool_call(self) -> None:
        self.check_runtime()
        self.budget.consume_tool()

    def refund_tool_call(self) -> None:
        """Compensate a tool reservation when execution setup cannot commit."""
        if self.budget.tool_calls > 0:
            self.budget.tool_calls -= 1

    def reserve_model_call(self) -> None:
        self.check_runtime()
        self.budget.consume_model()

    def reserve_recovery_cycle(self) -> None:
        self.check_runtime()
        self.budget.consume_recovery()

    @property
    def elapsed_seconds(self) -> float:
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
