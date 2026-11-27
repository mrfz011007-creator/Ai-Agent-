# AI Agent Architecture and Capability Boundaries

Last reviewed: 2026-11-27

## Purpose

AI Agent is an offline-first local agent runtime with model-backed conversation and controlled task execution. Models propose responses, plans, and tool requests; application code owns authorization, execution, evidence, and task acceptance.

This document describes implemented behavior, not a security certification or a guarantee that external providers are available.

## Request flow

1. Local intent handlers process supported local queries without a model call.
2. Ordinary questions and explanations use `ConversationService`. This path receives bounded conversation history and has no tool-execution interface.
3. Explicit imperative requests enter `GoalRunner`.
4. The planner returns a JSON plan. `PlanDecoder` validates its structure, dependencies, criteria, tool names, and declared capabilities.
5. The executor receives the active task's validated execution contract and only the tool-catalog entries named in `allowed_tools`; it proposes one tool call. The proposal remains untrusted data, and an out-of-contract tool is rejected before routing.
6. `ToolRouter` checks task state and attempt identity, execution contract, capability policy, schema, GuardEngine, PolicyEngine, user confirmation, global budget, and task-local tool quota.
7. The registered handler runs. The router records redacted evidence; the verifier and acceptance gate decide whether the task/plan can complete.

A model response never grants itself tool permission. A normal chat response is not an executed task.


## Agent working standard

`AGENT_RULES.md` defines Think First, Simplicity First, Surgical Changes, and Evidence-Based Completion. The fixed repository-root file is loaded with a strict size bound by `core/agent_rules.py`; only its compact, marked prompt section is included in model-planning and model-execution prompts. The loader does not accept a user-supplied path and falls back to minimal built-in guidance if the file or marked section is unavailable or invalid.

The rules are model-facing guidance, not an enforcement boundary. The deterministic `Planner` in `core/plan.py` and the existing validators continue to validate plans and execution contracts. `Orchestrator` still controls bounded task sequencing and requires evidence plus the acceptance gate before completion. `ToolRouter`, GuardEngine, PolicyEngine, capability checks, confirmation, budgets, and the strict Python sandbox remain authoritative.

The audited file-access implementation is `security/workspace.py`. No `path_aman` module or function exists in this source tree; references to that name should not be treated as an implemented component.

## Memory model

| Layer | Storage | Scope and limits |
|---|---|---|
| Conversation history | `conversation_messages` table in SQLite | Per conversation ID: latest 12 messages; prompt context: 24,000 characters; individual message: 12,000 characters; global retained rows: 2,048 |
| Durable facts/preferences/decisions | Workspace `memory.json` store | Global, project, and task scopes are isolated during retrieval and writes |
| Task execution experience | Same structured memory store | Recorded as bounded, ephemeral experience with task/evidence provenance |
| Reflection candidates | Bounded local review queue | Model-generated candidates remain pending until human approval; evidence is revalidated when approved |

Conversation history is not automatically promoted into project/task memory. The default conversation ID is `default`. Set `AI_AGENT_CONVERSATION_ID` to a distinct stable value when sessions must be separated. Clear a conversation through `StateStore.clear_conversation(conversation_id)`; a dedicated CLI command is not currently provided.

Memory supplied to a model is reference data only. It does not prove a task succeeded and cannot alter execution permissions or policies.

## Model providers and reliability

- Default route: configured OpenRouter model, then one configurable OpenRouter backup, then Gemini.
- Direct Gemini mode is supported through `AI_AGENT_MODEL_PROVIDER=gemini`.
- OpenRouter and Gemini use `AI_AGENT_MODEL_TIMEOUT_SECONDS` (default 45 seconds; accepted range 1–120 seconds). The synchronous Gemini client is closed after each request to release its HTTP connection.
- The Gemini SDK receives its timeout in milliseconds through `HttpOptions.timeout`.
- Retry/failover is limited to classified authentication, rate-limit, service-unavailable, and network failures. Invalid requests stop without fallback.
- A confirmed invalid API key (HTTP 401 / invalid-key response) skips additional models under the same provider so the same credential is not sent repeatedly. Model-specific HTTP 403 still permits trying the configured backup model.
- Each actual provider request consumes the model-call budget. Provider routing is bounded by the configured route list.
- The dependency range is constrained to `google-genai>=1.0.0,<3.0.0` to avoid an unreviewed major SDK migration.

Timeouts are per provider request. The full fallback chain can take longer than one request timeout because routes are sequential; this is not a hard end-to-end deadline.

### Per-request model identity

Direct chat uses `generate_text_with_metadata()` internally while the legacy `generate_text()` and `chat()` interfaces continue returning plain strings. The fallback gateway returns metadata from the route that actually succeeded, not from the configured primary route.

- OpenRouter actual model identity is read from the provider response's `model` field; its response `id` is retained as an optional request reference.
- Gemini actual model identity is read from the response's `model_version` / `modelVersion` metadata when supplied.
- Requested/configured model and provider-reported model remain separate. If actual model metadata is absent, the CLI labels identity as unverified and shows only the requested model; it does not infer actual identity from configuration or model-generated text.
- Provider identity and metadata are rendered by runtime/CLI outside model-generated answer content. API keys, prompts, and full provider responses are not logged or displayed.
- The model prompt contains configured route information only. The actual model field is returned with the provider response after generation, so the model must not infer current-request identity from the configured route list. The system instruction directs identity questions to the separate runtime metadata section.
- The CLI labels this block as runtime metadata, fixes provider-line formatting, and reports identity as unverified when the provider omits the actual-model field.

This verifies the identity reported by the provider API response. It does not independently audit provider-side infrastructure or guarantee the underlying serving hardware.

## Task lifecycle and recovery

Plans are stored with their task IDs, acceptance criteria, project ID, and context. Tasks retain status, attempt count, execution contract, tool-call count, evidence references, and checkpoints in SQLite.

Interrupted `RUNNING` or `VERIFYING` tasks are moved to `WAITING` and are not blindly replayed. A safe retry requires explicit idempotency, remaining execution-contract retries, and available recovery budget. Ambiguous side effects require reconciliation evidence or human intervention. Successful tool output alone does not complete a task; the acceptance gate must pass.

## Implemented capabilities

- Direct model-backed conversation with bounded persistent history.
- Local intent handling for supported device/runtime queries.
- Plan creation for explicit action requests, dependency ordering, one-task-at-a-time execution, checkpoints, resume, and recovery.
- Tool selection from the runtime registry, schema validation, execution-contract and capability enforcement, permission policy, confirmation, budgets, evidence, and verification.
- Workspace file read/write/search/patch operations through descriptor-relative secure helpers and bounded resource limits.
- A small, non-interpreting command allowlist. The command fallback is not a general shell.
- Public read-only HTTPS text retrieval through the guarded `fetch_url` tool after confirmation.
- Static Python syntax/required-definition validation through `validasi_python`; it parses source and does not execute it.
- A separate strict Python Runner for hosts that pass live OS namespace and privilege-drop checks.

## Known limitations

- Termux and Ubuntu in PRoot do not satisfy the strict OS isolation requirements for `jalankan_python`; that tool remains denied there.
- The hardened Termux command path is defense in depth, not an OS sandbox. It does not create independent filesystem or network namespaces.
- The Python Runner enforces per-file write limits, not an aggregate workspace disk quota. Approved code may modify or delete files inside its configured workspace.
- The model may misunderstand a request or return factually incorrect content. Tests validate boundaries and deterministic behaviors, not general intelligence or factual correctness.
- CI uses deterministic fakes/mocks for provider-dependent tests. CI success does not prove live API credentials, model availability, network quality, or response quality on a device.
- Static Python validation does not prove runtime correctness because generated code is not executed by that validator.
- Provider failover is sequential; there is no strict total wall-clock deadline across the entire chain.
- A passing test suite is not a security certification.

## Verification gates

GitHub Actions runs the repository tests on Python 3.11 and 3.14, dependency consistency checks, Python compilation, Ruff, Bandit, and the Python Runner bootstrap smoke test.

Termux interactive smoke testing is intentionally deferred until Stage 8 implementation and its GitHub Actions workflow pass. The Termux test must be recorded separately from CI and any live-provider evaluation. Do not label unexecuted device checks as passed.

## Security invariants

Do not weaken or bypass `write_file_secure`, workspace descriptor/no-follow checks, GuardEngine, PolicyEngine, ToolRouter, execution contracts, capability checks, confirmation requirements, budgets, evidence validation, or Python Runner fail-closed behavior to make a test pass or enable unsupported Termux execution.
