from __future__ import annotations

from dataclasses import dataclass

from core.contracts import Budget


@dataclass
class BudgetManager:
    budget: Budget

    def reserve_tool_call(self) -> None:
        self.budget.consume_tool()

    @property
    def remaining_tool_calls(self) -> int:
        return max(0, self.budget.max_tool_calls - self.budget.tool_calls)

    @property
    def remaining_recovery_cycles(self) -> int:
        return max(0, self.budget.max_recovery_cycles - self.budget.recovery_cycles)

    def snapshot(self) -> dict[str, int]:
        return {
            "max_tool_calls": self.budget.max_tool_calls,
            "tool_calls": self.budget.tool_calls,
            "remaining_tool_calls": self.remaining_tool_calls,
            "max_recovery_cycles": self.budget.max_recovery_cycles,
            "recovery_cycles": self.budget.recovery_cycles,
            "remaining_recovery_cycles": self.remaining_recovery_cycles,
        }
