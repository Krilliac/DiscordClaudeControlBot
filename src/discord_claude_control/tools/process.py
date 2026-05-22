"""Process tools: launch_app (detached) and kill_process (by pid or name)."""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from ._helpers import error_result, text_result

log = logging.getLogger(__name__)

_LAUNCH_DESC = (
    "Launch a program as a detached child process so it survives the bot. "
    "Returns the new PID. Use this to open apps, IDEs, browsers, etc."
)

_KILL_DESC = (
    "Terminate one or more processes. `name_or_pid` is either a numeric PID "
    "or an executable name (matched case-insensitively, all matching "
    "processes are terminated). Uses psutil.terminate() (SIGTERM-equivalent "
    "on Windows)."
)

_LAUNCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "args": {"type": "array", "items": {"type": "string"}, "default": []},
    },
    "required": ["path"],
}

_KILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name_or_pid": {"type": "string"}},
    "required": ["name_or_pid"],
}


async def _launch_app(args: dict[str, Any]) -> dict[str, Any]:
    path = args.get("path")
    extra_args = args.get("args", [])
    if not isinstance(path, str) or not path:
        return error_result("path must be a non-empty string")
    if not isinstance(extra_args, list) or not all(isinstance(a, str) for a in extra_args):
        return error_result("args must be a list of strings")
    popen_kwargs: dict[str, Any] = {"close_fds": True}
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        if flags:
            popen_kwargs["creationflags"] = flags
    try:
        proc = subprocess.Popen([path, *extra_args], **popen_kwargs)
    except (FileNotFoundError, PermissionError, OSError) as e:
        return error_result(f"launch failed: {e}")
    return text_result(f"launched {path} as pid {proc.pid}")


async def _kill_process(args: dict[str, Any]) -> dict[str, Any]:
    target = args.get("name_or_pid")
    if not isinstance(target, str) or not target:
        return error_result("name_or_pid must be a non-empty string")
    try:
        import psutil
    except ImportError:
        return error_result("psutil is not installed")

    killed: list[tuple[int, str]] = []
    if target.isdigit():
        pid = int(target)
        try:
            p = psutil.Process(pid)
            name = p.name()
            p.terminate()
            killed.append((pid, name))
        except psutil.NoSuchProcess:
            return error_result(f"no process with pid {pid}")
        except psutil.AccessDenied:
            return error_result(f"access denied terminating pid {pid}")
    else:
        target_lower = target.lower()
        for p in psutil.process_iter(["pid", "name"]):
            try:
                info_name = p.info["name"]
                if info_name and info_name.lower() == target_lower:
                    p.terminate()
                    killed.append((p.info["pid"], info_name))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not killed:
            return error_result(f"no process matched name {target!r}")

    listing = "\n".join(f"  pid={pid} name={name}" for pid, name in killed)
    return text_result(f"terminated {len(killed)} process(es):\n{listing}")


def build_process_tools() -> dict[str, SdkMcpTool[Any]]:
    return {
        "launch_app": tool("launch_app", _LAUNCH_DESC, _LAUNCH_SCHEMA)(_launch_app),
        "kill_process": tool("kill_process", _KILL_DESC, _KILL_SCHEMA)(_kill_process),
    }
