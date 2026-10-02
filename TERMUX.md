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
