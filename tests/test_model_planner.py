from core.model_planner import ModelPlanService
from core.plan import PlanGraphError


def test_model_plan_service_decodes_valid_json():
    service = ModelPlanService(
        lambda _: '{"goal":"build APK","tasks":[{"task_id":"inspect","title":"Inspect","dependencies":[]}],"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
    )
    proposal = service.propose("build APK")
    assert proposal.goal == "build APK"
    assert proposal.tasks[0].task_id == "inspect"


def test_model_plan_service_rejects_non_json():
    service = ModelPlanService(lambda _: "not json")
    try:
        service.propose("build APK")
        assert False, "invalid model output must be rejected"
    except PlanGraphError:
        pass


def test_model_plan_service_model_output_cannot_inject_execution_fields():
    service = ModelPlanService(
        lambda _: '{"goal":"build","tasks":[{"task_id":"x","title":"run","dependencies":[],"command":"rm -rf /"}],"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
    )
    proposal = service.propose("build")
    assert not hasattr(proposal.tasks[0], "command")


def test_model_plan_service_exposes_authoritative_tool_catalog():
    prompts = []
    catalog = {
        "tulis_file": {
            "description": "Write a workspace file",
            "capabilities": ["workspace.write"],
            "parameters": {"type": "object"},
        }
    }

    service = ModelPlanService(
        lambda prompt: prompts.append(prompt) or (
            '{"goal":"write","tasks":[{"task_id":"write","title":"Write file",'
            '"dependencies":[],"execution_contract":{"objective":"write",'
            '"allowed_tools":["tulis_file"],"allowed_capabilities":["workspace.write"],'
            '"max_tool_calls":1,"retry_limit":0,"evidence_required":true,'
            '"completion_conditions":[{"type":"evidence_success","task_id":"write"}]}}],'
            '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
        ),
        tool_catalog=catalog,
    )

    proposal = service.propose("write")
    assert proposal.tasks[0].execution_contract.allowed_tools == ("tulis_file",)
    assert '"tulis_file"' in prompts[0]


def test_model_plan_service_rejects_unknown_tool_from_runtime_catalog():
    service = ModelPlanService(
        lambda _: (
            '{"goal":"write","tasks":[{"task_id":"write","title":"Write file",'
            '"dependencies":[],"execution_contract":{"objective":"write",'
            '"allowed_tools":["write_file"],"allowed_capabilities":["workspace.write"],'
            '"max_tool_calls":1,"retry_limit":0,"evidence_required":true,'
            '"completion_conditions":[{"type":"evidence_success","task_id":"write"}]}}],'
            '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
        ),
        tool_catalog={
            "tulis_file": {
                "capabilities": ["workspace.write"],
                "parameters": {"type": "object"},
            }
        },
    )

    try:
        service.propose("write")
        assert False, "unknown planner tool must be rejected before execution"
    except PlanGraphError as error:
        assert "Unknown tool in execution contract" in str(error)


def test_model_plan_service_rejects_contract_capability_not_declared_by_tool():
    service = ModelPlanService(
        lambda _: (
            '{"goal":"read","tasks":[{"task_id":"read","title":"Read file",'
            '"dependencies":[],"execution_contract":{"objective":"read",'
            '"allowed_tools":["baca_file"],"allowed_capabilities":["workspace.write"],'
            '"max_tool_calls":1,"retry_limit":0,"evidence_required":true,'
            '"completion_conditions":[{"type":"evidence_success","task_id":"read"}]}}],'
            '"acceptance_criteria":[{"type":"all_tasks_completed"}]}'
        ),
        tool_catalog={
            "baca_file": {
                "capabilities": ["workspace.read"],
                "parameters": {"type": "object"},
            }
        },
    )

    try:
        service.propose("read")
        assert False, "contract capabilities must match the selected tools"
    except PlanGraphError as error:
        assert "capability exceeds selected tools" in str(error)
