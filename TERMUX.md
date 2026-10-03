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
export AI_AGENT_WORKSPACE_ROOT="$PWD"
export AI_AGENT_STATE_DB="$PWD/agent_state.sqlite3"
```

These variables can be added to your Termux shell profile later if needed.

## 4. Install Python dependencies

```bash
python -m pip install -r requirements.txt
python -m pip check
python -m compileall -q .
```

The second command verifies installed dependency consistency. The third command verifies that all Python sources compile.

## 5. Run the repository tests

```bash
python -m pytest -q
```

A clean environment should report the repository test suite as passing.

## 6. Configure Gemini keys

Use environment variables; do not put API keys into source files:

```bash
export GEMINI_API_KEY_1='YOUR_KEY_1'
export GEMINI_API_KEY_2='YOUR_KEY_2'
export GEMINI_API_KEY_3='YOUR_KEY_3'
```

At least one key is required for model execution. The gateway can rotate among the three configured credentials when a provider failure is classified as retryable or credential-related.

## 7. Start the agent

```bash
python agent.py
```

Then enter a request at:

```text
Agent >
```

Use `exit` to stop the process.

## Termux execution behavior

Android/Termux is detected before the Linux namespace/chroot sandbox path is attempted. The agent therefore uses its hardened execution fallback on Termux.

The fallback keeps command execution inside the configured workspace, does not invoke a shell, strips credential-like environment variables, and applies process resource limits where the platform exposes them. Network isolation is not guaranteed in the fallback mode.

For that reason, treat Termux execution as a controlled local environment, not as a full security sandbox.

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

Then run:

```bash
python agent.py
```

Use only the non-destructive smoke inputs:

```text
lokasi saya
lihat file
exit
```

Check the memory file size again after the process exits. Do not delete `memory.json` or `agent_state.sqlite3` during this validation.

The current defaults are 8 MiB automatic compaction, 500 active non-durable execution experiences, and 5 retained historical revisions per memory identity. Override them only when there is a concrete storage requirement.


## Reflection quota policy

Execution experiences are persisted locally without requiring another model call. By default, reflection is performed for failed executions only to avoid spending a Gemini call on every successful tool invocation.

Set the following only when success-based reflection is required:

```bash
export AI_AGENT_REFLECT_ON_SUCCESS=1
```

The default keeps the existing experience → reflection → memory-candidate pipeline for failures while preserving a low-quota path for successful execution.
