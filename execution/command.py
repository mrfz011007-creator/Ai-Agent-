from __future__ import annotations

import subprocess
from pathlib import Path


def run_command(
    *,
    command: str,
    cwd: str,
    timeout: float = 900.0,
) -> dict:
    """Execute one bounded process; authorization is owned by ToolRouter."""
    try:
        completed = subprocess.run(
            command.split(),
            cwd=str(Path(cwd).resolve()),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "success": False,
            "status": "TIMEOUT",
            "exit_code": None,
            "stdout": error.stdout or "",
            "stderr": error.stderr or "TIMEOUT",
        }
    except OSError as error:
        return {
            "success": False,
            "status": "EXECUTION_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }

    return {
        "success": completed.returncode == 0,
        "status": "SUCCESS" if completed.returncode == 0 else "FAILED",
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
