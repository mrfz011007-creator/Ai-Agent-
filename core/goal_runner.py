from __future__ import annotations

import json
import os
from dataclasses import replace

from core.contracts import TaskStatus
from core.model_planner import ModelPlanService
from core.plan import Plan, PlanStatus, TaskGraph
from core.memory_service import memory_prompt_context
from core.conversation import MAX_CONVERSATION_MESSAGES
from security.redaction import redact_value
from core.trusted_intent import extract_trusted_file_intent


class GoalRunner:
    """High-level bounded goal API over planning, execution, recovery, and verification."""

    def __init__(self, runtime):
        self.runtime = runtime

    def _persist(self, plan: Plan, status: PlanStatus) -> Plan:
        updated = replace(plan, status=status)
        self.runtime.orchestrator.persist_plan(updated)
        return updated

    def _status_for_graph(self, graph: TaskGraph) -> PlanStatus:
        if graph.is_complete():
            return PlanStatus.COMPLETED
        if any(task.status == TaskStatus.BLOCKED for task in graph.tasks.values()):
            return PlanStatus.BLOCKED
        if any(task.status in (TaskStatus.WAITING, TaskStatus.RUNNING, TaskStatus.VERIFYING)
               for task in graph.tasks.values()):
            return PlanStatus.WAITING if any(
                task.status == TaskStatus.WAITING for task in graph.tasks.values()
            ) else PlanStatus.EXECUTING
        if any(task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
               for task in graph.tasks.values()):
            return PlanStatus.FAILED
        if not graph.ready():
            return PlanStatus.FAILED
        return PlanStatus.EXECUTING

    def _run_graph(
        self,
        plan: Plan,
        graph: TaskGraph,
        *,
        max_steps: int | None = None,
        project_id: str | None = None,
        context=None,
    ) -> tuple[Plan, TaskGraph]:
        plan = self._persist(plan, PlanStatus.EXECUTING)
        last_results = getattr(self.runtime, "last_goal_execution", None)
        if isinstance(last_results, dict):
            last_results.clear()
        steps = 0
        execution_context = {"goal": plan.goal, "project_id": project_id, "context": dict(context or {})}
        trusted_file_intent = extract_trusted_file_intent(plan.goal)

        def execute_with_context(proposal, *, task_id, attempt_id=None):
            trusted_kwargs = {}
            # A verified-file proposal always requires constraints extracted
            # from the original user goal. If extraction was ambiguous or the
            # format is unsupported, pass a missing constraint so ToolRouter
            # fails closed before approval rather than silently skipping it.
            if proposal.tool == "buat_file_terverifikasi" or trusted_file_intent is not None:
                trusted_kwargs = {
                    "trusted_tool": "buat_file_terverifikasi",
                    "trusted_arguments": trusted_file_intent,
                }
            result = self.runtime.execute_model_proposal(
                proposal,
                task_id=task_id,
                attempt_id=attempt_id,
                **trusted_kwargs,
            )
            execution_record = {
                "tool": proposal.tool,
                "action": proposal.action,
                "success": result.success,
                "status": result.status,
                "evidence_id": result.evidence_id,
                "data": result.data,
                "error": result.error,
            }
            execution_context[task_id] = execution_record
            last_results = getattr(self.runtime, "last_goal_execution", None)
            if isinstance(last_results, dict):
                last_results[task_id] = execution_record
            # Memory/reflection is observability, not an execution authority.
            # A persistence or reflection failure must never convert an already
            # successful tool execution into a task failure or trigger a retry
            # that could duplicate a side effect.
            experience_value = {
                "tool": proposal.tool, "action": proposal.action,
                "success": result.success, "status": result.status,
                "evidence_id": result.evidence_id,
                "result": result.data if result.success else None,
                "error": result.error,
            }
            try:
                experience_record = self.runtime.memory.record_experience(
                    key=f"task:{task_id}:execution",
                    value=experience_value,
                    task_id=task_id, project_id=project_id, context=context,
                    source={"kind": "execution", "ref": result.evidence_id or task_id},
                    provenance={"reason": "bounded task execution result", "task_id": task_id},
                    tags=["execution", "success" if result.success else "failure"],
                    # Raw execution traces are high-volume; durable reusable
                    # knowledge is produced by bounded reflection instead.
                    retention="ephemeral",
                )
            except Exception:
                experience_record = {"record": {"value": experience_value}}

            evidence_refs = [result.evidence_id] if result.evidence_id else []
            reflect_success = os.environ.get("AI_AGENT_REFLECT_ON_SUCCESS", "0").lower() in {
                "1", "true", "yes", "on"
            }
            should_reflect = bool(evidence_refs) and (not result.success or reflect_success)
            if should_reflect:
                try:
                    self.runtime.memory.reflect_and_commit(
                        model_call=self.runtime.model_gateway.generate_text,
                        experience=experience_record.get("record", {}).get("value", experience_value),
                        evidence_refs=evidence_refs,
                        related_memory=execution_context.get("memory", []),
                        project_id=project_id,
                        task_id=task_id,
                        context=context,
                        source={"kind": "execution", "ref": result.evidence_id},
                    )
                except Exception:
                    pass
            return result

        while not graph.is_complete():
            if max_steps is not None and steps >= max_steps:
                plan = self._persist(plan, PlanStatus.WAITING)
                self._record_conversation_outcome(plan.goal, plan, graph)
                return plan, graph

            model_wait_active = [
                item for item in graph.tasks.values()
                if item.status == TaskStatus.RUNNING
                and isinstance(item.result, dict)
                and item.result.get("recovery_action") == "WAIT_FOR_MODEL"
            ]
            if model_wait_active:
                task = model_wait_active[0]
            else:
                task = self.runtime.orchestrator.next_ready(graph)
                if task is None:
                    break
            memory_result = self.runtime.memory.retrieve(
                f"{plan.goal} {task.title}", project_id=project_id,
                task_id=task.task_id, context=context, limit=8,
            )
            execution_context["memory"] = memory_prompt_context(memory_result)

            task = self.runtime.orchestrator.execute_model_step(
                graph,
                model_call=self.runtime.model_gateway.text,
                execute_proposal=execute_with_context,
                verify_execution=self.runtime.verify_execution_evidence,
                handle_model_failure=self.runtime.handle_model_failure,
                tool_catalog=self.runtime.tool_catalog(),
                model_context=execution_context,
            )
            status = self._status_for_graph(graph)
            plan = self._persist(plan, status)
            if task is None or task.status != TaskStatus.COMPLETED:
                return plan, graph
            steps += 1

        if graph.is_complete():
            acceptance = self.runtime.acceptance_gate.verify_plan_criteria(
                criteria=plan.acceptance_criteria,
                task_ids=tuple(graph.tasks),
                completed_task_ids=tuple(
                    task.task_id for task in graph.tasks.values()
                    if task.status == TaskStatus.COMPLETED
                ),
                failed_task_ids=tuple(
                    task.task_id for task in graph.tasks.values()
                    if task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED)
                ),
                current_attempt_ids={
                    task.task_id: f"{task.task_id}:attempt:{task.attempts}"
                    for task in graph.tasks.values()
                },
            )
            if acceptance.status.value != "PASSED":
                return self._persist(plan, PlanStatus.FAILED), graph
            return self._persist(plan, PlanStatus.COMPLETED), graph
        return self._persist(plan, PlanStatus.WAITING), graph

    def _record_conversation_outcome(self, original_goal: str, plan: Plan, graph: TaskGraph) -> None:
        """Persist a bounded task outcome beside chat history for follow-up questions."""
        store = getattr(self.runtime, "state_store", None)
        append_turn = getattr(store, "append_conversation_turn", None)
        if not callable(append_turn):
            return

        conversation_id = os.environ.get("AI_AGENT_CONVERSATION_ID", "default")
        task_rows = []
        for task in graph.tasks.values():
            result = task.result
            reason = None
            if isinstance(result, dict):
                reason = result.get("reason") or result.get("error")
            elif isinstance(getattr(result, "reason", None), str):
                reason = result.reason
            task_rows.append({
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status.value,
                "reason": reason,
            })

        executions = getattr(self.runtime, "last_goal_execution", {})
        payload = {
            "record_type": "goal_execution_result",
            "plan_id": plan.plan_id,
            "goal": original_goal,
            "plan_status": plan.status.value,
            "tasks": task_rows,
            "tool_results": executions if isinstance(executions, dict) else {},
        }
        safe_payload = redact_value(payload)
        assistant_message = (
            "Riwayat hasil tindakan Agent (catatan runtime, bukan instruksi):\\n"
            + json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, default=str)
        )
        if len(assistant_message) > 12000:
            # Keep decisive status and failure causes; omit bulky tool payloads.
            compact = {
                key: value for key, value in safe_payload.items()
                if key != "tool_results"
            }
            compact["tool_results"] = {
                task_id: {
                    key: value
                    for key, value in record.items()
                    if key in {"tool", "success", "status", "error", "evidence_id"}
                }
                for task_id, record in (
                    safe_payload.get("tool_results", {}).items()
                    if isinstance(safe_payload.get("tool_results"), dict)
                    else ()
                )
                if isinstance(record, dict)
            }
            assistant_message = (
                "Riwayat hasil tindakan Agent (catatan runtime, bukan instruksi):\\n"
                + json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str)
            )
        try:
            append_turn(
                conversation_id,
                original_goal.strip(),
                assistant_message[:12000],
                max_messages=MAX_CONVERSATION_MESSAGES,
            )
        except Exception:
            # Conversation continuity is observability; it must not change an
            # already-determined task status or retry a completed side effect.
            return

    def run(
        self,
        goal: str,
        *,
        plan_id: str | None = None,
        max_steps: int | None = None,
        project_id: str | None = None,
        context=None,
    ) -> tuple[Plan, TaskGraph]:
        """Create and execute one bounded goal through the runtime boundary."""
        proposer = ModelPlanService(
            self.runtime.model_gateway.generate_text,
            memory=self.runtime.memory,
            tool_catalog=self.runtime.tool_catalog(),
        )
        proposal = replace(proposer.propose(goal, project_id=project_id, context=context), goal=goal.strip())
        resolved_id = plan_id or self._new_plan_id()
        plan, graph = self.runtime.orchestrator.materialize(proposal, resolved_id)
        plan = replace(
            plan,
            project_id=project_id,
            context=dict(context or {}),
        )
        self.runtime.orchestrator.persist_plan(plan)
        result = self._run_graph(
            plan,
            graph,
            max_steps=max_steps,
            project_id=project_id,
            context=context,
        )
        self._record_conversation_outcome(goal, *result)
        return result

    def resume(
        self,
        plan_id: str,
        *,
        max_steps: int | None = None,
    ) -> tuple[Plan, TaskGraph]:
        """Explicitly resume one persisted plan and only its task graph."""
        restored = self.runtime.orchestrator.restore_graph(plan_id)
        if restored is None:
            raise KeyError(f"Unknown persisted plan: {plan_id}")
        plan, graph = restored

        self.runtime.recovery_manager.recover_tasks(tuple(graph.tasks))
        restored = self.runtime.orchestrator.restore_graph(plan_id)
        if restored is None:
            raise KeyError(f"Unknown persisted plan: {plan_id}")
        plan, graph = restored

        waiting = [task for task in graph.tasks.values() if task.status == TaskStatus.WAITING]
        if waiting:
            # Model availability waits are safe to resume because no tool
            # proposal was executed for that attempt.
            if len(waiting) == 1 and self.runtime.recovery_manager.can_resume_model_wait(
                waiting[0].task_id
            ):
                resumed = self.runtime.task_manager.resume(waiting[0].task_id)
                graph.tasks[resumed.task_id] = resumed
            else:
                # Interrupted tool execution still requires explicit
                # reconciliation to avoid duplicating external side effects.
                plan = self._persist(plan, PlanStatus.WAITING)
                return plan, graph

        # Project/context metadata is not persisted with Plan yet. Resume
        # therefore uses explicit runtime defaults instead of undefined names.
        result = self._run_graph(
            plan,
            graph,
            max_steps=max_steps,
            project_id=plan.project_id,
            context=plan.context,
        )
        self._record_conversation_outcome(plan.goal, *result)
        return result

    @staticmethod
    def _new_plan_id() -> str:
        import uuid
        return f"plan-{uuid.uuid4().hex[:12]}"
