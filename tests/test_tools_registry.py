from __future__ import annotations

from discord_claude_control.config import ToolsConfig
from discord_claude_control.tools import ALL_TOOL_NAMES, MCP_SERVER_NAME, build_tools


def _config(enabled: tuple[str, ...]) -> ToolsConfig:
    return ToolsConfig(
        input_auth_mode="autonomous",
        restrict_paths=False,
        allow_roots=(),
        enabled=enabled,
        output_truncate_at=1500,
        powershell_default_timeout_s=30,
    )


def test_no_tools_when_enabled_subset_unknown() -> None:
    # Only an unknown name -> nothing registered.
    server, allowed = build_tools(_config(("does_not_exist",)))
    assert server is None
    assert allowed == []


def test_empty_enabled_means_all_tools() -> None:
    server, allowed = build_tools(_config(()))
    assert server is not None
    # Every advertised tool should be in the allowed list.
    for name in ALL_TOOL_NAMES:
        assert f"mcp__{MCP_SERVER_NAME}__{name}" in allowed


def test_subset_only_registers_those_tools() -> None:
    server, allowed = build_tools(_config(("read_file", "list_dir")))
    assert server is not None
    assert allowed == [
        f"mcp__{MCP_SERVER_NAME}__read_file",
        f"mcp__{MCP_SERVER_NAME}__list_dir",
    ]


def test_tools_use_namespaced_mcp_name() -> None:
    _, allowed = build_tools(_config(("screenshot",)))
    assert allowed == [f"mcp__{MCP_SERVER_NAME}__screenshot"]
