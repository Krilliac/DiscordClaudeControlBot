"""
Tool registry: collects all SdkMcpTool objects, builds the in-process MCP
server, and computes the SDK's `allowed_tools` list.

The bot calls `build_tools(config.tools)` once at startup and feeds the
result into `ClaudeAgentOptions(mcp_servers=..., allowed_tools=...)`. Tools
not listed in `config.tools.enabled` are not registered.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server

from ..audit import audit_wrap
from ..config import InputAuthMode, ToolsConfig
from ._helpers import error_result
from .confirm import describe_action, request_confirmation
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

DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({"kill_process", "launch_app"})
INPUT_TOOLS: frozenset[str] = frozenset({"move_mouse", "click", "type_text", "press_key"})

_Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _needs_confirm(tool_name: str, mode: InputAuthMode) -> bool:
    if mode == "autonomous":
        return False
    if mode == "confirm_destructive":
        return tool_name in DESTRUCTIVE_TOOLS
    if mode == "confirm_all":
        return tool_name in DESTRUCTIVE_TOOLS or tool_name in INPUT_TOOLS
    return False


def _confirm_wrap(
    tool_name: str,
    handler: _Handler,
    *,
    mode: InputAuthMode,
    allowed_user_id: int,
) -> _Handler:
    """Wrap `handler` so it asks for reaction confirmation before running,
    when the mode says this tool needs it. Otherwise pass through unchanged."""
    if not _needs_confirm(tool_name, mode):
        return handler

    async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
        approved = await request_confirmation(
            user_id=allowed_user_id,
            action=describe_action(tool_name, args),
        )
        if not approved:
            return error_result(f"user declined to confirm {tool_name}")
        return await handler(args)

    return wrapped


def build_tools(
    config: ToolsConfig,
    *,
    allowed_user_id: int = 0,
) -> tuple[McpSdkServerConfig | None, list[str]]:
    enabled = set(config.enabled) if config.enabled else set(ALL_TOOL_NAMES)
    unknown = enabled - set(ALL_TOOL_NAMES)
    if unknown:
        log.warning("ignoring unknown tools in config: %s", sorted(unknown))

    if config.input_auth_mode != "autonomous" and allowed_user_id <= 0:
        log.warning(
            "input_auth_mode=%r requires a valid allowed_user_id; "
            "falling back to 'autonomous'",
            config.input_auth_mode,
        )

    registered: dict[str, SdkMcpTool[Any]] = {}

    if "run_powershell" in enabled:
        registered["run_powershell"] = build_shell_tool(config)

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

    # Compose decorators inside-out: audit logs every call (raw args);
    # confirm gates the call before the real handler runs.
    effective_mode: InputAuthMode = (
        config.input_auth_mode if allowed_user_id > 0 else "autonomous"
    )
    finalized: dict[str, SdkMcpTool[Any]] = {}
    for name, tool_obj in registered.items():
        handler = _confirm_wrap(
            name, tool_obj.handler, mode=effective_mode, allowed_user_id=allowed_user_id
        )
        handler = audit_wrap(name, handler)
        finalized[name] = replace(tool_obj, handler=handler)

    server = create_sdk_mcp_server(MCP_SERVER_NAME, tools=list(finalized.values()))
    allowed = [f"mcp__{MCP_SERVER_NAME}__{name}" for name in finalized]
    log.info(
        "registered %d tools (mode=%s): %s",
        len(finalized),
        effective_mode,
        sorted(finalized),
    )
    return server, allowed
