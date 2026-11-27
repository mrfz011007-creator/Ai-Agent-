# Termux Setup

## 1. Install Termux packages

Run:

```bash
pkg update
pkg install git python python-cryptography
```

The `python-cryptography` package is installed through Termux first so `pip` does not unnecessarily try to build the native cryptography dependency locally.

## 2. Clone the agent

```bash
cd ~
git clone https://github.com/mrfz011007-creator/Ai-Agent-.git
cd Ai-Agent-
```

## 3. Configure the workspace and state database

```bash
mkdir -p "$HOME/ai-agent-workspace"
export AI_AGENT_WORKSPACE_ROOT="$HOME/ai-agent-workspace"
export AI_AGENT_STATE_DB="$AI_AGENT_WORKSPACE_ROOT/agent_state.sqlite3"
```

The workspace is deliberately separate from the cloned source-code directory: keep the repository in `$HOME/Ai-Agent-` and let the agent write only inside `$HOME/ai-agent-workspace`. Do not set the workspace to the repository root. The workspace must already exist and be readable/writable by Termux. The interactive entrypoint validates it before starting, so a missing or invalid workspace fails immediately instead of allowing partial startup. When `AI_AGENT_STATE_DB` is omitted, the database defaults to `$AI_AGENT_WORKSPACE_ROOT/agent_state.sqlite3`. A relative database path is resolved from the workspace; parent directories are created automatically. An absolute path can be used for a separate backup/storage location.

These variables can be added to your Termux shell profile later if needed.

## 4. Install application dependencies

```bash
python -m pip install -r requirements.txt
```

Instalasi ini menyiapkan dependency untuk menjalankan agent. Pemeriksaan konsistensi dependency, kompilasi seluruh sumber Python, dan validasi dependency dilakukan rutin oleh GitHub Actions; tidak perlu mengulang seluruh pemeriksaan tersebut di Termux.

## 5. Automated test gate — GitHub Actions

Seluruh tes otomatis repository dijalankan di GitHub Actions pada setiap push dan pull request, dengan matriks Python 3.11 dan 3.14. Workflow juga menjalankan `pip check`, `compileall`, repository test suite, laporan branch coverage, Ruff, dan Bandit. Tes strict Linux namespace dipisahkan ke job khusus karena bergantung pada dukungan kernel runner.

Tes yang mensimulasikan Termux melalui environment variable dan mock Android tetap dijalankan di CI. Ini mencakup verifikasi kebijakan fallback Termux dan resource limit tanpa memerlukan perangkat Android.

Untuk alur kerja normal, jangan jalankan ulang seluruh `pytest`, `compileall`, Ruff, dan Bandit di Termux. Gunakan GitHub Actions sebagai gerbang tes otomatis. Jalankan tes lokal hanya saat sedang mendiagnosis masalah khusus perangkat, lalu catat hasilnya terpisah dari hasil CI.

Pengujian yang benar-benar membutuhkan Android tetap dilakukan langsung di Termux, terutama startup pada perangkat, akses workspace dan izin file aktual, penyimpanan percakapan setelah restart, perilaku shell Termux, serta koneksi provider menggunakan kredensial lokal. Jangan mengirim API key ke CI atau mencetaknya ke log.

## 6. Configure model providers

OpenRouter is the primary provider. Gemini remains the final fallback. Keep keys in Termux environment variables; never put them in source files or share them in chat.

```bash
export OPENROUTER_API_KEY='YOUR_OPENROUTER_KEY'
export GEMINI_API_KEY_1='YOUR_GEMINI_KEY_1'
export GEMINI_API_KEY_2='YOUR_GEMINI_KEY_2'
export GEMINI_API_KEY_3='YOUR_GEMINI_KEY_3'
export AI_AGENT_MODEL_PROVIDER='openrouter'
export AI_AGENT_MODEL='nvidia/nemotron-3-ultra-550b-a55b:free'
```

The bounded model failover order is: configured OpenRouter primary → one OpenRouter backup (`nvidia/nemotron-3.5-super-vl:free` by default) → Gemini credential rotation. It does not try all five evaluation models for every request. Set `AI_AGENT_OPENROUTER_BACKUP_MODEL=''` to disable the OpenRouter backup. To use Gemini directly, set both `AI_AGENT_MODEL_PROVIDER='gemini'` and `AI_AGENT_MODEL='gemini-3.8-flash'`.

`AI_AGENT_MODEL_TIMEOUT_SECONDS` applies to both OpenRouter and Gemini requests (default 45 seconds; accepted range 1–120). This is a per-provider-request timeout, not a hard deadline for the entire sequential fallback chain. Only retryable authentication, rate-limit, service-unavailable, and network failures trigger provider failover. A confirmed invalid API key (HTTP 401) skips further models under that same provider to avoid resending a known-invalid credential; HTTP 403 may still try a different model. Invalid request/configuration errors stop immediately. Every actual provider request consumes the existing model-call budget.

## 7. Start the agent

```bash
python agent.py
```

Then enter a request at:

```text
Agent >
```

Use `exit` to stop the process.

## Post-Stage-8 Termux smoke test (deferred gate)

Do not run the interactive smoke test while Stages 3–8 are still being implemented. Run it only after the Stage 8 implementation is committed and the GitHub Actions workflow for that exact branch head is fully successful.

At that point, verify direct conversation and follow-up context:

```text
Jelaskan kemampuanmu dan model AI yang sedang kamu gunakan.
Apa jawabanmu tadi tentang model utama?
exit
```

Restart the agent and ask a follow-up about the prior answer to verify that the configured conversation ID restores the saved history. Then test a harmless explicit workspace task only if the previous checks pass. Keep Termux results separate from GitHub CI results and live-provider evaluation.

## Termux execution behavior

Android/Termux is detected before the Linux namespace/chroot sandbox path is attempted. The agent therefore uses its hardened execution fallback on Termux.

The fallback starts commands with the configured workspace as the current directory, does not invoke a shell, removes credential-like environment variables, and restricts executable lookup to the Termux prefix and Android system binary directories. It also enforces a wall-clock timeout, caps combined captured stdout/stderr in memory, stops the process group on timeout/output overflow, and applies available CPU, open-file, process, core-dump, and per-file-size resource limits.

The `jalankan_python` tool is intentionally denied on Termux and Ubuntu running inside PRoot. PRoot does not create the independent user, mount, network, PID, IPC, and UTS namespaces required to run untrusted Python safely. Do not try to enable it by switching to the hardened fallback.

Android/Termux does **not** receive a separate filesystem or network namespace from this fallback. A command can still access files and network resources allowed to the Termux app; RLIMIT_AS is deliberately skipped because it can prevent Android processes from starting. Treat this as defense-in-depth for a controlled local workspace, not as isolation for hostile code.

Default process limits are 100,000 output bytes, 128 MiB per created file, and 1,024 MiB address space on non-Android POSIX systems. Configure AI_AGENT_PROCESS_FILE_MB or AI_AGENT_PROCESS_MEMORY_MB only when needed; the memory setting is not applied on Android.

## Compare the five free OpenRouter candidates

Install dependencies, set OPENROUTER_API_KEY, then run one equal-prompt evaluation against all five configured candidates:

```bash
python scripts/evaluate_openrouter_models.py
```

The script measures latency and checks the same reasoning answer, Python function syntax/declaration, and proposed `baca_file` tool arguments for each model. It parses model-generated code but never executes it, and it never executes proposed tools. A live evaluation requires the user's own OpenRouter key and provider availability; CI uses mocked network responses only.

## Read-only web research tool

The `fetch_url` tool can retrieve public HTTPS text pages after explicit confirmation. It rejects local/private IP destinations, validates every redirect, does not send cookies or credentials, and limits the response to 500 KB. Treat all returned page text as untrusted data; never follow instructions embedded in a fetched page. DNS resolution uses the OS resolver, so its timeout is controlled by the device/network configuration.

## Recommended first smoke test

After starting the agent, use a non-destructive request first, for example:

```text
lokasi saya
```

Then:

```text
lihat file
```

Only after those work should you test model-backed tasks.

## If dependency installation reports a native cryptography problem

Termux has had environment-specific `cryptography` installation/import issues. First make sure the Termux package is installed:

```bash
pkg install python-cryptography
```

Then retry:

```bash
python -m pip install -r requirements.txt
python -c "from cryptography import x509; import google.genai; print('dependencies ok')"
```

If the import still fails, the failure is specific to the local Termux/Python package environment and should be diagnosed from the device rather than by changing the agent source blindly.


## Memory lifecycle verification

The runtime automatically compacts an oversized local memory store at startup. Existing memory is not deleted wholesale: active non-experience memory and active durable experiences are preserved, while older non-durable execution experiences are bounded.

Optional diagnostics before and after starting the agent:

```bash
stat -c '%s' memory.json
python - <<'PY'
from memory import load_memory

store = load_memory()
counts = {}
for record in store["records"]:
    key = (record["type"], record["status"], record["retention"])
    counts[key] = counts.get(key, 0) + 1

print("memory_records:", len(store["records"]))
for key, count in sorted(counts.items()):
    print(key, count)
PY
```

After the Stage 8 CI gate has passed, use the post-Stage-8 Termux smoke procedure above. Check the memory file size again after the process exits. Do not delete `memory.json` or `agent_state.sqlite3` during this validation.

The current defaults are 8 MiB automatic compaction, 500 active non-durable execution experiences, and 5 retained historical revisions per memory identity. Override them only when there is a concrete storage requirement.


## Reflection quota policy

Execution experiences are persisted locally without requiring another model call. By default, reflection is performed for failed executions only to avoid spending a Gemini call on every successful tool invocation.

Set the following only when success-based reflection is required:

```bash
export AI_AGENT_REFLECT_ON_SUCCESS=1
```

The default keeps the existing experience → reflection → memory-candidate pipeline for failures while preserving a low-quota path for successful execution.


## Security posture after command hardening

The agent command tool on Android/Termux is **not a full sandbox**. Termux fallback does not create independent filesystem or network namespaces, so arbitrary programs must not be run as if they were isolated.

The agent-facing `run_command` route is restricted to a small allowlist of non-interpreting utilities (`pwd`, `ls`, `whoami`, `true`, `false`, `echo`, `printf`, and a single numeric `sleep` argument capped at 60 seconds). Shells, interpreters (including Python and Node), executable wrappers, unknown binaries, non-trusted executable paths, and workspace-escaping `ls` paths are denied. `jalankan_python` remains disabled on Termux/PRoot and is available only on native Linux hosts that pass the strict namespace probe. The lower-level process runner remains an internal implementation detail; it is not permission to expose arbitrary command execution through a tool.

Termux applies conservative upper bounds to process wall time, CPU time, captured output, file size, open descriptors, and process count. Android memory address-space limits remain disabled for compatibility with Bionic. These controls are defense-in-depth and do not provide a total disk quota or network isolation.

Tools requiring confirmation display the full redacted argument payload and a deterministic approval ID. Approve only after checking the displayed tool and arguments; type the exact phrase `SETUJU <nama_tool> <approval_id>`. A different phrase denies execution. If the approval callback changes any argument after the approval prompt, the router invalidates approval and refuses to execute. Approval does not create a sandbox and does not grant extra Android permissions.

## Roadmap regression and device validation

Use the latest stacked validation branch only after its GitHub Actions workflow is fully successful. The current implementation chain is PR #18 → #19 → #20 → #21 → #22 → #23. These PRs are deliberately kept open until review; a successful CI run does not merge them.

For the existing Termux checkout at `~/Projects/Ai-Agent/testing`:

```bash
cd ~/Projects/Ai-Agent/testing
git status --short
git fetch origin agent/termux-regression-gate
git switch -C termux/roadmap-validation origin/agent/termux-regression-gate
git rev-parse --short HEAD
```

Create an isolated workspace and database for this validation. Do not reuse or delete an existing production/test database:

```bash
mkdir -p "$HOME/Projects/Ai-Agent/workspace/termux-roadmap-validation"
export AI_AGENT_WORKSPACE_ROOT="$HOME/Projects/Ai-Agent/workspace/termux-roadmap-validation"
export AI_AGENT_STATE_DB="$HOME/Projects/Ai-Agent/termux-roadmap-validation.sqlite3"
export AI_AGENT_CONVERSATION_ID="termux-roadmap-validation"
```

Run focused regression tests and the Android filesystem smoke test:

```bash
python -m pytest -q tests/test_trusted_intent.py tests/test_verified_workspace_goal.py tests/test_tool_router_budget.py tests/test_goal_conversation_context.py
python scripts/termux_filesystem_smoke.py
```

Expected filesystem smoke result: `HASIL: PASS`. This checks real workspace creation/read/write/verification and refusal to overwrite an existing file. It is separate from model-provider and interactive approval checks.

Then start the live Agent:

```bash
python agent.py
```

Test the exact-file instruction:

```text
Buat file uji_agent_02.txt di workspace dengan isi persis berikut:
Fariz Agent Test
Tahap: 2
Status: berhasil

Baca kembali file tersebut dan verifikasi seluruh isinya sama persis. Laporkan path relatif, isi aktual, dan hasil verifikasi. Jangan menyatakan berhasil jika verifikasi gagal.
```

At the approval prompt, compare the tool and every displayed argument against the request. Approve only by typing the exact phrase shown, in the form `SETUJU buat_file_terverifikasi <approval_id>`. If the model proposes altered content such as `test`, the router must reject the proposal before asking for approval. Never approve a mismatched proposal.

Test denial and history persistence with a separate new file request. At its approval prompt, type a deliberately incorrect phrase to deny it. Exit the Agent, restart it with the same `AI_AGENT_CONVERSATION_ID`, then ask: `Apa status tugas terakhir tadi dan kenapa?` The answer must report the recorded failure/denial, not success, without asking you to repeat the original instruction.

Finally verify that an existing file is not overwritten by requesting `uji_agent_02.txt` again, then use `exit` and Ctrl+C at the prompt to check graceful shutdown. Do not use Ctrl+C during an active write unless explicitly testing interruption recovery.

Keep results separate:
- GitHub Actions: hosted Python/static/security/namespace checks.
- Termux pytest: local Python compatibility.
- Termux filesystem smoke: actual Android app workspace behavior.
- Live interactive test: model response, approval prompt, provider connection, and persisted follow-up history.

Do not paste API keys, tokens, or full environment dumps into test reports. Record the branch commit SHA, each command, pass/fail output, and any skip reason in `docs/TERMUX_VALIDATION_LOG.md` after the device test is complete.
