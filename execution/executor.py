from __future__ import annotations

from core.contracts import ToolRequest, ToolResult
from execution.router import ToolRouter


class Executor:
    """Thin execution facade. All side effects still pass through ToolRouter."""

    def __init__(self, router: ToolRouter):
        self.router = router

    def execute(self, request: ToolRequest) -> ToolResult:
        return self.router.execute(request)
