# Security Model and Operating Boundaries

## Security goals

AI Agent is designed to keep model output untrusted and route tool requests through application-controlled validation and authorization. The project applies defense in depth; it is not a certified security boundary.

## Execution boundaries

- Model-generated tool proposals are data, not executable code. They must pass the registered tool schema, task execution contract, capability policy, GuardEngine, permission policy, and ToolRouter.
- Unknown-tool, workspace, task-contract, capability, guard, policy, and user-cancellation denials are persisted as redacted execution evidence when the state store is available. Raw tool arguments are not included in these denial records.
- The public `run_command` entry point and the registered command handler both re-check the command guard.
- The agent command allowlist is intentionally small and excludes shells, interpreters, launch wrappers, destructive commands, and unknown executables.
- `run_command` rejects interpreters, shells, and execution wrappers.
- `jalankan_python` is a separate, confirmation-gated tool. It runs one bounded UTF-8 workspace file only after user, mount, network, PID, IPC, and UTS namespaces pass a live probe; unsupported hosts fail closed and never fall back to the hardened command mode.
- Before untrusted Python starts, trusted system `setpriv` drops capability bounding, inheritable, and ambient sets, locks out root capability semantics, and sets `no_new_privs`. Interpreter-specific library paths are added only after this privilege drop.
- Python execution uses isolated mode (`-I -S -B`), caps source at 32 KiB, wall time at 30 seconds, output at 64 KiB, address space at 512 MiB, per-file writes at 16 MiB, process count at 32, and open descriptors at 64. The mount tree is made private before sandbox mounts are created; the workspace is the only persistent writable host mount, system/runtime roots are read-only, and `/tmp` is a capped private tmpfs.
- Generic, non-interpreter process commands may still use the hardened fallback when namespaces are unavailable. That fallback is not an OS sandbox and does not provide network isolation.

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
- The `fetch_url` tool performs read-only HTTPS GET requests to public addresses only. It rejects non-global IPv4/IPv6 answers, pins the connection to a validated address, revalidates every redirect, limits redirects to 3, applies a 10-second socket timeout, and caps response bodies at 500 KB.
- Web fetch rejects credentials in URLs, non-HTTPS schemes, non-standard ports, non-text content types, and redirects to private/local addresses. It does not send cookies, authorization headers, or user credentials.
- Fetched page text is untrusted external input and must never be treated as instructions or executable code. The tool requires explicit user confirmation and records the execution through ToolRouter.
- DNS lookup uses the operating system resolver; its timeout behavior is controlled by the host resolver and is not a strict end-to-end wall-clock guarantee.

## Known limitations

- Android Termux and Ubuntu running inside PRoot do not provide the Linux namespace isolation required for the strict Python runner; `jalankan_python` is denied there. The hardened fallback is defense in depth only.
- The Python runner rejects the host root (`/`) as its writable workspace. It enforces a per-file size limit, not an aggregate workspace disk quota. Approved code can still modify or delete files inside the configured workspace; run only code you intend to execute.
- Same-user processes and a compromised Android/Termux account are outside the protection boundary of the application-level workspace checks.
- Resource limits and application guards reduce risk but do not guarantee protection from every kernel, runtime, dependency, or device vulnerability.
- Do not store API keys in workspace files, prompts, model output, screenshots, or issue reports.

## Verification

The CI workflow runs the test suite on Python 3.11 and 3.14, compile checks, Ruff, and Bandit. Security claims must remain aligned with the actual tests and the execution environment. A passing CI run is not a security certification.
