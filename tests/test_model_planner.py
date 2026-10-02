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
