"""run_powershell tool: executes a PowerShell command with a timeout."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from ..config import ToolsConfig
from ._helpers import error_result, spill_if_large, text_result

log = logging.getLogger(__name__)

_DESCRIPTION = (
    "Run a PowerShell command on the host Windows PC and return its stdout, "
    "stderr, and exit code. Use this for any Windows administrative task: "
    "querying system state with cmdlets, listing services, inspecting "
    "processes, reading the registry, etc. The command runs via "
    "`powershell.exe -NoProfile -NonInteractive -Command <command>`. Output "
    "is truncated with a marker. Raise `timeout_s` for long-running commands."
)


def build_shell_tool(tools_config: ToolsConfig) -> SdkMcpTool[Any]:
    truncate_at = tools_config.output_truncate_at
    default_timeout = tools_config.powershell_default_timeout_s

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "PowerShell command to execute"},
            "timeout_s": {
                "type": "integer",
                "description": "Timeout in seconds",
                "default": default_timeout,
            },
        },
        "required": ["command"],
    }

    async def _run(args: dict[str, Any]) -> dict[str, Any]:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return error_result("command must be a non-empty string")
        timeout_s = args.get("timeout_s", default_timeout)
        if not isinstance(timeout_s, int) or isinstance(timeout_s, bool) or timeout_s <= 0:
            return error_result("timeout_s must be a positive integer")

        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return error_result("PowerShell not found on PATH (looked for powershell.exe and pwsh)")

        try:
            proc = await asyncio.create_subprocess_exec(
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return error_result(f"failed to launch PowerShell: {e}")

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                log.warning("PowerShell process did not exit after kill()")
            return error_result(f"command exceeded {timeout_s}s timeout and was killed")

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode if proc.returncode is not None else -1

        stdout_rendered = await spill_if_large(
            stdout, threshold=truncate_at, filename="powershell-stdout.txt"
        )
        stderr_rendered = await spill_if_large(
            stderr, threshold=truncate_at, filename="powershell-stderr.txt"
        )
        body = (
            f"exit_code: {exit_code}\n"
            f"--- stdout ---\n{stdout_rendered}\n"
            f"--- stderr ---\n{stderr_rendered}"
        )
        return text_result(body)

    return tool("run_powershell", _DESCRIPTION, schema)(_run)
