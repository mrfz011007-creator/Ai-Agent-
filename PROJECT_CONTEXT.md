# AI Agent Fariz — Project Context

Updated: 2026-11-27

## Repository checkpoint

- Repository: `mrfz011007-creator/Ai-Agent-`
- Active branch: `agent/security-hardening-workspace`
- Pull request: [#17](https://github.com/mrfz011007-creator/Ai-Agent-/pull/17)
- PR base: `agent/security-hardening`
- PR status at last inspection: OPEN, not merged.
- Previous verified head: `b6884f7b66c7502a8d7c88484028b64966753217`
- Do not merge PR #17 without explicit user approval.

## Current task: per-request provider/model identity

Goal: show the provider and actual model based on metadata returned by the provider for the exact request. Never infer actual identity from configured models.

### Confirmed architecture

1. OpenRouter reads actual identity from the response `model` field and the optional request reference from `id`.
2. Gemini reads actual identity from `model_version` / `modelVersion` when returned.
3. `ModelGateway` and `FallbackModelGateway` return metadata from the provider route that actually succeeded.
4. `ConversationService` and `AgentRuntime` carry that metadata separately from answer text.
5. Provider response metadata is available to the runtime only after the answer has been generated. The model itself must not guess its current identity from configuration.
6. The CLI is the authoritative display for the provider-reported identity. It must label the metadata block as runtime output, separate from model-authored text.
7. When actual model metadata is missing, display the requested model only as requested/configured and mark actual identity unverified.

### Root cause found in the pre-fix Termux test

At commit `b6884f7`, the model received the configured primary/fallback description but not the current response's actual-model metadata. The old prompt did not explicitly prevent guesses from that configuration, so the model listed configured models while the CLI independently showed provider-returned metadata. The CLI also had an escaped-newline formatting defect before the provider line.

### Security and implementation constraints

- Preserve provider routing, retry/fallback behavior, call budgets, ToolRouter, PolicyEngine, GuardEngine, execution contracts, workspace protections, evidence checks, and strict Python sandbox behavior.
- Do not insert untrusted model-generated claims as provider metadata.
- Do not report actual identity when the provider response omits it.
- Termux and PRoot cannot run the strict namespace Python runner in the known device environment; keep it fail-closed.
- CI mocks provider responses. CI success does not prove live provider availability or certify security.

## Validation split

### GitHub — can be completed remotely

- Audit source and tests.
- Make the minimal prompt/CLI changes.
- Add regression tests for truthful identity instructions, metadata-present display, and metadata-missing display.
- Run Python 3.11/3.14 CI, dependency consistency, compilation, Ruff, Bandit, and the optional namespace job.
- Record the exact head commit and CI outcomes.

### Termux — requires the user's Android device

- Pull the verified branch head.
- Activate the workspace/database and locally stored provider key without printing the key.
- Run `python agent.py`.
- Ask which provider/model handled the current request.
- Confirm the model text directs the user to the separate runtime metadata block and that the CLI shows the provider-reported model without a literal `\\nProvider` prefix.
- Repeat with a response path where actual-model metadata is unavailable or use an existing deterministic test; never infer model identity from configuration.

## Current status

- Stages 1–3: source audit and targeted code/test changes prepared for GitHub; completion depends on CI after commit.
- Stage 4: pending GitHub Actions for the new commit.
- Stage 5: pending live rerun on Termux after the CI gate.
- Stage 6: this file and `docs/TERMUX_VALIDATION_LOG.md` are the continuity records; final verification remains pending until the device retest is recorded.

## Working rules

Think First. Simplicity First. Surgical Changes. Evidence-Based Completion.
Do not claim tests passed unless their actual results are available. Do not merge PR #17 without explicit approval.
