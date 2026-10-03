"""Smoke tests for the current tool registry.

This file intentionally avoids the removed legacy agent.jalankan_tool/proses_tool
API and does not execute confirmation-gated tools.
"""

from registry import get_tool, get_tool_catalog


def test_safe_tool_registry():
    tool = get_tool("lokasi")

    assert tool is not None
    assert tool["permission"] == "safe"
    assert tool["idempotent"] is True

    result = tool["func"]()
    assert result["success"] is True
    assert result["status"] == "SUCCESS"


def test_unknown_tool_is_not_registered():
    assert get_tool("tool_tidak_dikenal") is None


def test_write_tools_remain_confirmation_gated():
    catalog = get_tool_catalog()

    assert catalog["buat_file"]["permission"] == "confirm"
    assert catalog["tulis_file"]["permission"] == "confirm"
    assert catalog["patch_file"]["permission"] == "confirm"
