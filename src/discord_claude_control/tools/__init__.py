"""
Tool registry: collects all SdkMcpTool objects, builds the in-process MCP
server, and computes the SDK's `allowed_tools` list.

The bot calls `build_tools(config.tools)` once at startup and feeds the
result into `ClaudeAgentOptions(mcp_servers=..., allowed_tools=...)`. Tools
not listed in `config.tools.enabled` are not registered.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server

from ..audit import audit_wrap
from ..config import ToolsConfig
from .files import build_file_tools
from .input import build_input_tools
from .process import build_process_tools
from .screen import build_screenshot_tool
from .shell import build_shell_tool

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "dcc"

ALL_TOOL_NAMES: tuple[str, ...] = (
    "run_powershell",
    "read_file",
    "write_file",
    "list_dir",
    "screenshot",
    "move_mouse",
    "click",
    "type_text",
    "press_key",
    "launch_app",
    "kill_process",
)


def build_tools(
    config: ToolsConfig,
) -> tuple[McpSdkServerConfig | None, list[str]]:
    enabled = set(config.enabled) if config.enabled else set(ALL_TOOL_NAMES)
    unknown = enabled - set(ALL_TOOL_NAMES)
    if unknown:
        log.warning("ignoring unknown tools in config: %s", sorted(unknown))

    if config.input_auth_mode != "autonomous":
        log.warning(
            "input_auth_mode=%r is not implemented yet; running tools as 'autonomous'",
            config.input_auth_mode,
        )

    registered: dict[str, SdkMcpTool[Any]] = {}

    if "run_powershell" in enabled:
        registered["run_powershell"] = build_shell_tool()

    file_tools = build_file_tools(config)
    for name in ("read_file", "write_file", "list_dir"):
        if name in enabled:
            registered[name] = file_tools[name]

    if "screenshot" in enabled:
        registered["screenshot"] = build_screenshot_tool()

    input_tools = build_input_tools()
    for name in ("move_mouse", "click", "type_text", "press_key"):
        if name in enabled:
            registered[name] = input_tools[name]

    process_tools = build_process_tools()
    for name in ("launch_app", "kill_process"):
        if name in enabled:
            registered[name] = process_tools[name]

    if not registered:
        log.info("no tools enabled; agent will run text-only")
        return None, []

    # Wrap every tool's handler so audit.log captures the invocation.
    audited = {
        name: replace(tool_obj, handler=audit_wrap(name, tool_obj.handler))
        for name, tool_obj in registered.items()
    }

    server = create_sdk_mcp_server(MCP_SERVER_NAME, tools=list(audited.values()))
    allowed = [f"mcp__{MCP_SERVER_NAME}__{name}" for name in audited]
    log.info("registered %d tools: %s", len(audited), sorted(audited))
    return server, allowed
