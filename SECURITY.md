# Security Model and Operating Boundaries

## Security goals

AI Agent is designed to keep model output untrusted and route tool requests through application-controlled validation and authorization. The project applies defense in depth; it is not a certified security boundary.

## Execution boundaries

- Model-generated tool proposals are data, not executable code. They must pass the registered tool schema, task execution contract, capability policy, GuardEngine, permission policy, and ToolRouter.
- Unknown-tool, workspace, task-contract, capability, guard, policy, and user-cancellation denials are persisted as redacted execution evidence when the state store is available. Raw tool arguments are not included in these denial records.
- The public `run_command` entry point and the registered command handler both re-check the command guard.
- The agent command allowlist is intentionally small and excludes shells, interpreters, launch wrappers, destructive commands, and unknown executables.
- `jalankan_python` is disabled. Do not enable arbitrary Python or shell execution on Termux/PRoot.
- Linux namespace execution is used only when user, mount, and network namespaces are successfully probed. Otherwise the process uses a restricted hardened fallback; that fallback is not an OS sandbox and does not provide network isolation.

## Workspace filesystem

- Workspace paths are relative and reject traversal and ambiguous separators.
- File access uses directory descriptors, no-follow opens, regular-file checks, and hard-link checks.
- Writes use a temporary file in the pinned parent directory followed by atomic replacement; the existing inode is not opened for writing.
- File size, search result, file count, total search bytes, entry count, directory count, and traversal depth are bounded.
- Hard-link race coverage is skipped only on platforms where `os.link` is unavailable; Linux CI must keep this test active.

## Credentials and network

- Provider credentials are read from environment variables and are removed from child process environments.
- Tool output and approval arguments are recursively redacted, including values stored under credential-like dictionary keys.
- OpenRouter requests use a fixed HTTPS endpoint, bounded request/response sizes, a timeout, and a redirect handler that refuses all redirects. This prevents bearer credentials from being forwarded to a redirect destination.
- The project does not yet provide a general-purpose web-fetch tool. Do not treat the OpenRouter provider connection as a general browsing capability.

## Known limitations

- Android Termux and Ubuntu running inside PRoot do not provide the Linux namespace isolation required for the strict sandbox. The hardened fallback is defense in depth only.
- Same-user processes and a compromised Android/Termux account are outside the protection boundary of the application-level workspace checks.
- Resource limits and application guards reduce risk but do not guarantee protection from every kernel, runtime, dependency, or device vulnerability.
- Do not store API keys in workspace files, prompts, model output, screenshots, or issue reports.

## Verification

The CI workflow runs the test suite on Python 3.11 and 3.14, compile checks, Ruff, and Bandit. Security claims must remain aligned with the actual tests and the execution environment. A passing CI run is not a security certification.
