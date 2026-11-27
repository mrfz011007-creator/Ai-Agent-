#!/usr/bin/env python3
"""Termux filesystem smoke test; temporary files remain inside workspace."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tools  # noqa: E402
from core.trusted_intent import extract_trusted_file_intent  # noqa: E402

EXPECTED = "Fariz Agent Test\nTahap: 1\nStatus: berhasil"

def main() -> int:
    configured = os.environ.get("AI_AGENT_WORKSPACE_ROOT")
    if not configured:
        print("[FAIL] AI_AGENT_WORKSPACE_ROOT belum diatur")
        return 1
    workspace = Path(configured).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        print("[FAIL] Workspace bukan direktori")
        return 1
    print("=== TERMUX FILESYSTEM SMOKE TEST ===")
    print(f"Workspace: {workspace}")
    with tempfile.TemporaryDirectory(prefix=".fariz-agent-fs-smoke-", dir=workspace) as temp:
        rel_dir = Path(temp).resolve().relative_to(workspace).as_posix()
        rel_file = f"{rel_dir}/roundtrip.txt"
        result = tools.buat_file(rel_file)
        if not result.get("success"):
            print("[FAIL] buat_file:", result)
            return 1
        print("[PASS] buat_file")
        snapshot = tools.baca_file(rel_file)
        if not snapshot.get("success") or snapshot.get("isi") != "":
            print("[FAIL] baca_file awal:", snapshot)
            return 1
        print("[PASS] baca_file awal")
        result = tools.tulis_file(rel_file, EXPECTED, expected_sha256=snapshot["sha256"])
        if not result.get("success"):
            print("[FAIL] tulis_file:", result)
            return 1
        print("[PASS] tulis_file")
        result = tools.baca_file(rel_file)
        if not result.get("success") or result.get("isi") != EXPECTED:
            print("[FAIL] baca/verifikasi:", result)
            return 1
        print("[PASS] baca_file setelah tulis")
        print("[PASS] perbandingan isi persis")
        verified_path = f"{rel_dir}/verified.txt"
        result = tools.buat_file_terverifikasi(verified_path, EXPECTED)
        if not result.get("success") or not result.get("verified"):
            print("[FAIL] buat_file_terverifikasi:", result)
            return 1
        print("[PASS] buat -> tulis -> baca -> verifikasi")
        duplicate = tools.buat_file_terverifikasi(verified_path, "different content")
        if duplicate.get("success") is not False or duplicate.get("code") != "FILE_ALREADY_EXISTS":
            print("[FAIL] proteksi file existing:", duplicate)
            return 1
        print("[PASS] file existing tidak ditimpa")

        # The interactive CLI is line-based and strips terminal input. Exercise
        # the trusted parser directly so the final newline is tested as data.
        request = (
            "Buatkan file termux_trailing_newline.txt dengan isi persis: "
            "Fariz Agent\n"
        )
        trusted = extract_trusted_file_intent(request)
        expected_newline_content = "Fariz Agent\n"
        if trusted is None or trusted.get("isi") != expected_newline_content:
            print("[FAIL] trusted intent trailing newline:", trusted)
            return 1
        newline_path = f"{rel_dir}/trailing-newline.txt"
        newline_result = tools.buat_file_terverifikasi(
            newline_path, trusted["isi"]
        )
        if not newline_result.get("success") or not newline_result.get("verified"):
            print("[FAIL] newline file verification:", newline_result)
            return 1
        actual_bytes = (workspace / newline_path).read_bytes()
        expected_bytes = expected_newline_content.encode("utf-8")
        if actual_bytes != expected_bytes:
            print("[FAIL] trailing newline bytes:", repr(actual_bytes))
            return 1
        print("[PASS] trusted intent preserves final newline byte")

        print("Isi aktual:")
        print(result["isi_aktual"])
    print("HASIL: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
