from __future__ import annotations

from typing import Any, Mapping

from memory import remember, search_memory


class MemoryService:
    """Bounded adapter between agent lifecycle and persistent structured memory."""

    def __init__(self, *, project_id: str | None = None):
        self.project_id = project_id

    def retrieve(self, query: str, *, project_id: str | None = None,
                 task_id: str | None = None, context: Mapping[str, Any] | None = None,
                 limit: int = 8) -> dict[str, Any]:
        return search_memory(
            query,
            project_id=project_id if project_id is not None else self.project_id,
            task_id=task_id, context=context, limit=limit,
        )

    def record_experience(self, *, key: str, value: Any, task_id: str | None = None,
                          context: Mapping[str, Any] | None = None,
                          source: Mapping[str, Any] | str | None = None,
                          provenance: Mapping[str, Any] | None = None,
                          tags: list[str] | None = None) -> dict[str, Any]:
        return remember(
            key, value, memory_type="experience",
            project_id=self.project_id, task_id=task_id, context=context,
            source=source, provenance=provenance, tags=tags,
        )


def memory_prompt_context(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": item["record"].get("type"),
            "key": item["record"].get("key"),
            "value": item["record"].get("value"),
            "project_id": item["record"].get("project_id"),
            "task_id": item["record"].get("task_id"),
            "context": item["record"].get("context"),
            "source": item["record"].get("source"),
            "version": item["record"].get("version"),
            "updated_at": item["record"].get("updated_at"),
            "tags": item["record"].get("tags"),
        }
        for item in result.get("results", [])
    ]
