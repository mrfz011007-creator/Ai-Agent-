from core.execution_contract import ExecutionContract, ExecutionContractError
from core.model_planner import ModelPlanService
from core.plan import PlanGraphError, MAX_PLAN_TASKS


def test_model_planner_accepts_runtime_gateway_signature():
    class Gateway:
        def generate_text(self, prompt, *, system_instruction="", response_mime_type=None):
            assert prompt
            assert system_instruction == ""
            return (
                '{"goal":"build","tasks":[{"task_id":"inspect","title":"Inspect",'
                '"dependencies":[],"execution_contract":{"objective":"inspect",'
                '"allowed_tools":["lihat"],"allowed_capabilities":["workspace.read"],'
                '"max_tool_calls":1,"retry_limit":0,"evidence_required":true,'
                '"completion_conditions":[{"type":"evidence_success","task_id":"inspect"}]}}],'
                '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
            )

    proposal = ModelPlanService(Gateway().generate_text).propose("build")
    assert proposal.tasks[0].task_id == "inspect"


def test_execution_contract_rejects_plan_level_completion_conditions():
    for criterion_type in ("task_completed", "all_tasks_completed", "no_failed_tasks"):
        try:
            ExecutionContract(
                objective="inspect",
                allowed_tools=("lihat",),
                allowed_capabilities=("workspace.read",),
                completion_conditions=(
                    {"type": criterion_type, "task_id": "inspect"}
                    if criterion_type == "task_completed"
                    else {"type": criterion_type}
                ),
            )
            assert False, criterion_type
        except ExecutionContractError:
            pass


def test_model_plan_has_bounded_task_count():
    tasks = [
        {
            "task_id": f"task-{index}",
            "title": "Inspect",
            "dependencies": [],
        }
        for index in range(MAX_PLAN_TASKS + 1)
    ]
    payload = {
        "goal": "bounded",
        "tasks": tasks,
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    }
    try:
        from core.plan import PlanDecoder
        PlanDecoder.from_mapping(payload)
        assert False, "oversized model plan must be rejected"
    except PlanGraphError:
        pass


def test_model_plan_bounds_goal_and_task_metadata():
    from core.plan import MAX_GOAL_CHARS, MAX_TASK_ID_CHARS, MAX_TASK_TITLE_CHARS, PlanDecoder

    base = {
        "goal": "g",
        "tasks": [{"task_id": "task", "title": "title", "dependencies": []}],
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    }

    for field, limit in (
        ("goal", MAX_GOAL_CHARS),
    ):
        payload = dict(base)
        payload[field] = "x" * (limit + 1)
        try:
            PlanDecoder.from_mapping(payload)
            assert False, f"{field} over limit must be rejected"
        except PlanGraphError:
            pass

    for field, limit in (
        ("task_id", MAX_TASK_ID_CHARS),
        ("title", MAX_TASK_TITLE_CHARS),
    ):
        task = dict(base["tasks"][0])
        task[field] = "x" * (limit + 1)
        payload = dict(base)
        payload["tasks"] = [task]
        try:
            PlanDecoder.from_mapping(payload)
            assert False, f"{field} over limit must be rejected"
        except PlanGraphError:
            pass


def test_model_planner_bounds_historical_memory_context():
    from core.model_planner import MAX_PLANNER_CONTEXT_CHARS, ModelPlanService

    class Memory:
        def retrieve(self, *args, **kwargs):
            return {"results": [{
                "record": {
                    "type": "experience",
                    "key": "large",
                    "value": "x" * (MAX_PLANNER_CONTEXT_CHARS * 2),
                    "project_id": None,
                    "task_id": None,
                    "context": {},
                    "source": {"kind": "test", "ref": "memory"},
                    "provenance": {},
                    "importance": 0.5,
                    "confidence": 0.5,
                    "retention": "normal",
                    "summary": "large",
                    "version": 1,
                    "updated_at": "now",
                    "tags": [],
                }
            ]}

    seen = {}

    def model_call(prompt):
        seen["prompt"] = prompt
        return '{"goal":"bounded","tasks":[{"task_id":"inspect","title":"Inspect","dependencies":[]}],"acceptance_criteria":[{"type":"all_tasks_completed"}]}'

    proposal = ModelPlanService(model_call, memory=Memory()).propose("bounded")
    assert proposal.goal == "bounded"
    assert len(seen["prompt"]) < 30000
    assert "[MEMORY_CONTEXT_TRUNCATED]" in seen["prompt"]
