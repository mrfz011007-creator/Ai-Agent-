from memory import invalidate_memory, recall, search_memory, update_memory


def recall_memory(
    key,
    *,
    memory_type=None,
    project_id=None,
    task_id=None,
    include_invalidated=False,
):
    return recall(
        key,
        memory_type=memory_type,
        project_id=project_id,
        task_id=task_id,
        include_invalidated=include_invalidated,
    )


def search_memory_tool(
    query="",
    *,
    memory_type=None,
    project_id=None,
    task_id=None,
    context=None,
    tags=None,
    limit=10,
):
    return search_memory(
        query,
        memory_type=memory_type,
        project_id=project_id,
        task_id=task_id,
        context=context,
        tags=tags,
        limit=limit,
    )


def ambil_memory(key=None, query=None, **filters):
    if key is not None:
        return recall_memory(key, **filters)
    return search_memory_tool(query or "", **filters)


__all__ = [
    "ambil_memory",
    "invalidate_memory",
    "recall_memory",
    "search_memory_tool",
    "update_memory",
]
