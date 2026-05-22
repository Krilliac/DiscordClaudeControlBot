"""run_powershell tool: executes a PowerShell command with a timeout."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from ._helpers import error_result, text_result, truncate_output

log = logging.getLogger(__name__)

_DESCRIPTION = (
    "Run a PowerShell command on the host Windows PC and return its stdout, "
    "stderr, and exit code. Use this for any Windows administrative task: "
    "querying system state with cmdlets, listing services, inspecting "
    "processes, reading the registry, etc. The command runs via "
    "`powershell.exe -NoProfile -NonInteractive -Command <command>`. Output "
    "over ~1500 chars is truncated with a marker. Default timeout is 30 "
    "seconds; raise `timeout_s` for long-running commands."
)

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "PowerShell command to execute"},
        "timeout_s": {"type": "integer", "description": "Timeout in seconds", "default": 30},
    },
    "required": ["command"],
}


async def _run(args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return error_result("command must be a non-empty string")
    timeout_s = args.get("timeout_s", 30)
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

    body = (
        f"exit_code: {exit_code}\n"
        f"--- stdout ---\n{truncate_output(stdout)}\n"
        f"--- stderr ---\n{truncate_output(stderr)}"
    )
    return text_result(body)


def build_shell_tool() -> SdkMcpTool[Any]:
    return tool("run_powershell", _DESCRIPTION, _INPUT_SCHEMA)(_run)
