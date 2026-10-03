import pytest

from core.contracts import Task
from core.runtime import AgentRuntime


def test_task_persists_and_is_lazily_restored_after_runtime_restart(tmp_path):
    state_path = tmp_path / "state.sqlite3"
    task_id = "persisted-task-001"

    runtime1 = AgentRuntime.create(state_path=state_path)
    runtime1.task_manager.create(
        Task(task_id=task_id, title="Persistence regression")
    )

    assert runtime1.task_manager.get(task_id) is not None

    runtime2 = AgentRuntime.create(state_path=state_path)
    restored = runtime2.task_manager.get(task_id)

    assert restored is not None
    assert restored.task_id == task_id
    assert restored.title == "Persistence regression"


def test_create_rejects_task_already_persisted_but_not_loaded(tmp_path):
    state_path = tmp_path / "state.sqlite3"
    task = Task(task_id="duplicate-task-001", title="Duplicate")

    runtime1 = AgentRuntime.create(state_path=state_path)
    runtime1.task_manager.create(task)

    runtime2 = AgentRuntime.create(state_path=state_path)

    with pytest.raises(ValueError, match="Persisted task already exists"):
        runtime2.task_manager.create(
            Task(task_id=task.task_id, title="Duplicate again")
        )
