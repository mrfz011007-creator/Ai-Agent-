from __future__ import annotations

import sys
from pathlib import Path

import pytest

import execution.command as command
import execution.python_runner as python_runner
import execution.sandbox as sandbox
import security.guard as guard_module
from core.budget import Budget, BudgetManager
from core.contracts import ToolRequest
from execution.router import ToolRouter
from security.guard import GuardEngine
from security.policy import PolicyEngine
from verification.evidence import EvidenceStore


def _workspace(tmp_path: Path, monkeypatch) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    return workspace


def test_python_runner_passes_source_via_stdin_and_requires_namespace(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    source = "print('PRIVATE_SOURCE_MARKER')\n"
    (workspace / "main.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(python_runner, "namespace_sandbox_status", lambda: (True, "available"))
    monkeypatch.setattr(python_runner, "python_resource_limits_available", lambda: True)
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "status": "SUCCESS", "exit_code": 0,
                "stdout": "ok", "stderr": "", "sandbox_mode": "namespace"}

    monkeypatch.setattr(python_runner, "_run_command_unchecked", fake_run)
    result = python_runner.run_python_file("main.py")

    assert result["success"] is True
    assert captured["stdin_data"] == source.encode("utf-8")
    assert "PRIVATE_SOURCE_MARKER" not in captured["command"]
    assert captured["require_namespace"] is True
    assert captured["resource_profile"] == "python"
    assert captured["output_limit"] == python_runner.MAX_PYTHON_OUTPUT_BYTES
    assert captured["timeout"] == python_runner.DEFAULT_PYTHON_TIMEOUT_SECONDS
    assert captured["extra_readonly_roots"] == (sys.base_prefix,)
    assert result["script"] == "main.py"


def test_python_runner_fails_closed_without_namespace(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    (workspace / "main.py").write_text("print('must not run')", encoding="utf-8")
    monkeypatch.setattr(python_runner, "namespace_sandbox_status", lambda: (False, "namespace probe failed"))
    monkeypatch.setattr(python_runner, "_run_command_unchecked",
                        lambda **_kwargs: pytest.fail("must not execute without namespaces"))

    result = python_runner.run_python_file("main.py")
    assert result["success"] is False
    assert result["status"] == "SANDBOX_UNAVAILABLE"
    assert "namespace probe failed" in result["stderr"]


def test_python_runner_never_uses_hardened_fallback(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    (workspace / "main.py").write_text("print('must not run')", encoding="utf-8")
    monkeypatch.setattr(python_runner, "namespace_sandbox_status", lambda: (True, "available"))
    monkeypatch.setattr(python_runner, "python_resource_limits_available", lambda: True)

    def unavailable(*_args, **_kwargs):
        raise command.SandboxUnavailable("namespace disappeared")

    monkeypatch.setattr(command, "prepare_sandbox", unavailable)
    monkeypatch.setattr(command, "prepare_hardened",
                        lambda *_a, **_k: pytest.fail("no hardened fallback"))
    result = python_runner.run_python_file("main.py")

    assert result["success"] is False
    assert result["status"] == "SANDBOX_UNAVAILABLE"
    assert result["sandbox_mode"] == "unavailable"


def test_python_runner_rejects_traversal_symlink_and_hardlink(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    target = workspace / "main.py"
    target.write_text("print('safe')", encoding="utf-8")
    monkeypatch.setattr(python_runner, "namespace_sandbox_status", lambda: (True, "available"))
    monkeypatch.setattr(python_runner, "python_resource_limits_available", lambda: True)
    monkeypatch.setattr(python_runner, "_run_command_unchecked",
                        lambda **_kwargs: pytest.fail("unsafe source must not run"))

    assert python_runner.run_python_file("../main.py")["status"] == "WORKSPACE_ACCESS_DENIED"
    (workspace / "link.py").symlink_to(target)
    assert python_runner.run_python_file("link.py")["status"] == "WORKSPACE_ACCESS_DENIED"
    hardlink = workspace / "hardlink.py"
    try:
        hardlink.hardlink_to(target)
    except (AttributeError, OSError):
        pytest.skip("Hard links unavailable")
    assert python_runner.run_python_file("hardlink.py")["status"] == "WORKSPACE_ACCESS_DENIED"


def test_python_runner_rejects_large_and_non_utf8_source(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(python_runner, "namespace_sandbox_status", lambda: (True, "available"))
    monkeypatch.setattr(python_runner, "python_resource_limits_available", lambda: True)
    monkeypatch.setattr(python_runner, "_run_command_unchecked",
                        lambda **_kwargs: pytest.fail("invalid source must not run"))
    (workspace / "large.py").write_bytes(b"x" * (python_runner.MAX_PYTHON_SOURCE_BYTES + 1))
    assert python_runner.run_python_file("large.py")["status"] == "CODE_TOO_LARGE"
    (workspace / "invalid.py").write_bytes(bytes([0x70, 0x72, 0x69, 0x6e, 0x74, 0x28, 0x27, 0xff, 0x27, 0x29]))
    assert python_runner.run_python_file("invalid.py")["status"] == "SCRIPT_ENCODING_INVALID"


def test_python_resource_profile_uses_fixed_limits(monkeypatch):
    class FakeResource:
        RLIM_INFINITY = -1
        RLIMIT_CPU = 1
        RLIMIT_NOFILE = 2
        RLIMIT_NPROC = 3
        RLIMIT_CORE = 4
        RLIMIT_FSIZE = 5
        RLIMIT_AS = 6

        def __init__(self):
            self.values = {}

        def getrlimit(self, _name):
            return (-1, self.RLIM_INFINITY)

        def setrlimit(self, name, value):
            self.values[name] = value

    fake = FakeResource()
    monkeypatch.setattr(command, "resource", fake)
    command._apply_python_resource_limits(300)
    assert fake.values[FakeResource.RLIMIT_CPU] == (30, 30)
    assert fake.values[FakeResource.RLIMIT_NOFILE] == (64, 64)
    assert fake.values[FakeResource.RLIMIT_NPROC] == (32, 32)
    assert fake.values[FakeResource.RLIMIT_CORE] == (0, 0)
    assert fake.values[FakeResource.RLIMIT_FSIZE] == (16 * 1024 * 1024, 16 * 1024 * 1024)
    assert fake.values[FakeResource.RLIMIT_AS] == (512 * 1024 * 1024, 512 * 1024 * 1024)


def test_namespace_launcher_makes_mount_propagation_private(tmp_path, monkeypatch):
    import shutil

    if not shutil.which("unshare") or not shutil.which("chroot"):
        pytest.skip("Namespace tools are unavailable")
    monkeypatch.setattr(sandbox, "namespace_sandbox_status", lambda: (True, "available"))
    monkeypatch.setattr(sandbox, "_resolve_executable", lambda _argv: "/usr/bin/true")

    command, _env, root = sandbox.prepare_sandbox(["true"], cwd=str(tmp_path))
    launcher = Path(command[-1])
    try:
        lines = launcher.read_text(encoding="utf-8").splitlines()
        private_index = lines.index("mount --make-rprivate /")
        root_mount_index = lines.index('mount -t tmpfs -o size=128m,nr_inodes=8192 tmpfs "$ROOT"')
        first_bind_index = next(i for i, line in enumerate(lines) if "mount --bind" in line)
        assert private_index < root_mount_index < first_bind_index
    finally:
        sandbox.cleanup_sandbox(root)
    assert not launcher.exists()


def test_python_guard_denies_proot(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PROOT_LOADER", "/usr/libexec/proot")
    available, reason = sandbox.namespace_sandbox_status()
    assert not available
    assert "PRoot" in reason
    decision = GuardEngine(workspace).check(tool="jalankan_python", arguments={"nama": "main.py"})
    assert decision.allowed is False
    assert "PYTHON_SANDBOX_UNAVAILABLE" in decision.reason


def test_router_denies_python_before_confirmation_and_records_evidence(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    called = []
    registry = {
        "jalankan_python": {
            "capabilities": ["process.execute"],
            "permission": "confirm",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "nama": {"type": "string"},
                    "timeout": {"type": "number", "minimum": 0.1, "maximum": 30},
                },
                "required": ["nama"],
            },
            "func": lambda **_kwargs: called.append(True),
        }
    }
    monkeypatch.setattr(guard_module, "namespace_sandbox_status", lambda: (False, "unsupported host"))
    evidence = EvidenceStore()
    router = ToolRouter(
        registry_getter=registry.get, policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()), evidence=evidence,
        confirmation=lambda *_args: pytest.fail("no approval request on unsupported host"),
        guard=GuardEngine(workspace),
    )
    result = router.execute(ToolRequest("jalankan_python", "execute", {"nama": "main.py"}))
    assert result.success is False
    assert result.status == "guard_denied"
    assert result.evidence_id
    assert not called
    assert evidence.get(result.evidence_id).success is False


@pytest.mark.skipif(not sandbox.namespace_sandbox_available(),
                    reason="Strict Linux namespaces unavailable on this host")
def test_python_runner_real_namespace_smoke(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, monkeypatch)
    (workspace / "main.py").write_text(
        "from pathlib import Path\n"
        "Path('result.txt').write_text('workspace-ok', encoding='utf-8')\n"
        "Path('/tmp/agent-stage4-private').write_text('private', encoding='utf-8')\n"
        "print('namespace-ok')\n",
        encoding="utf-8",
    )
    result = python_runner.run_python_file("main.py", timeout=10)
    assert result["success"] is True, result
    assert "namespace-ok" in result["stdout"]
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "workspace-ok"
    assert not Path("/tmp/agent-stage4-private").exists()
