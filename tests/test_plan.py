from core.contracts import Task, TaskStatus
from core.plan import PlanGraphError, PlanStatus, Planner, TaskGraph


def test_plan_validates_dependencies_and_finds_ready_tasks():
    graph = TaskGraph()
    graph.add(Task("inspect", "inspect project"))
    graph.add(Task("build", "build project", dependencies=["inspect"]))
    graph.validate()
    ready = graph.ready()
    assert [task.task_id for task in ready] == ["inspect"]


def test_plan_rejects_unknown_dependency():
    graph = TaskGraph()
    graph.add(Task("build", "build", dependencies=["missing"]))
    try:
        graph.validate()
        assert False, "unknown dependency must fail"
    except PlanGraphError:
        pass


def test_plan_rejects_cycles():
    graph = TaskGraph()
    graph.add(Task("a", "a", dependencies=["b"]))
    graph.add(Task("b", "b", dependencies=["a"]))
    try:
        graph.validate()
        assert False, "cycle must fail"
    except PlanGraphError:
        pass


def test_plan_only_becomes_validated_after_graph_validation():
    planner = Planner()
    proposal = planner.propose(
        "build APK",
        [Task("inspect", "inspect"), Task("build", "build", dependencies=["inspect"])],
        ["APK exists and passes tests"],
    )
    plan = planner.materialize(proposal, "plan-1")
    assert plan.status == PlanStatus.VALIDATED
    assert plan.task_ids == ("inspect", "build")


def test_graph_completion_requires_all_tasks():
    graph = TaskGraph()
    first = graph.add(Task("a", "a", status=TaskStatus.COMPLETED))
    graph.add(Task("b", "b", dependencies=["a"]))
    assert not graph.is_complete()


def test_plan_decoder_rejects_malformed_model_plan():
    from core.plan import PlanDecoder
    try:
        PlanDecoder.from_mapping({"goal": "build", "tasks": [{"task_id": "a"}]})
        assert False, "malformed model plan must be rejected"
    except PlanGraphError:
        pass


def test_plan_decoder_validates_model_dependencies():
    from core.plan import PlanDecoder
    proposal = PlanDecoder.from_mapping({
        "goal": "build APK",
        "tasks": [
            {"task_id": "inspect", "title": "Inspect project"},
            {"task_id": "build", "title": "Build APK", "dependencies": ["inspect"]},
        ],
        "acceptance_criteria": ["APK exists"],
    })
    proposal.graph().validate()
    assert proposal.goal == "build APK"
    assert proposal.tasks[1].dependencies == ["inspect"]
