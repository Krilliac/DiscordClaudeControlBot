"""
Interactive first-run configuration wizard.

    python -m discord_claude_control.setup

Walks through every value the bot needs, validates them, and writes
`.env` (secrets) + `config.toml` (the rest). Both files are gitignored.
You can edit them by hand afterwards; this wizard just gets you started.

By default the wizard refuses to overwrite existing files; pass --force
to overwrite, or --env-path / --config-path to write somewhere else.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

_ENV_TEMPLATE = """\
# Written by `python -m discord_claude_control.setup`. Safe to edit by hand.
# This file is gitignored. Keep secrets here, not in config.toml.

DISCORD_BOT_TOKEN={bot_token}

# Anthropic auth: either set ANTHROPIC_API_KEY (pay-per-token) OR leave
# blank and run `claude /login` once on this PC for subscription mode.
{api_key_line}
"""

_CONFIG_TEMPLATE = """\
# Written by `python -m discord_claude_control.setup`. Safe to edit by hand.
# Documented defaults live in config.toml.example.

[discord]
# Developer Mode in Discord -> right-click -> Copy ID.
allowed_user_id    = {user_id}
allowed_channel_id = {channel_id}
allowed_guild_id   = {guild_id}

[agent]
# Model: any Claude model ID. Pin to a specific version if you want
# stability; use a family alias like "claude-opus-4-7" to track the latest.
model = "{model}"
max_tool_calls_per_message = 20
conversation_db_path = "conversation.db"
# Override the bot's system prompt -- pick at most one:
#   system_prompt      = "..." (inline)
#   system_prompt_path = "prompts/mine.txt" (read from a file)

[session]
# After this many minutes of silence the bot releases its power request
# and lets the PC go to Modern Standby. Discord stays connected through.
idle_timeout_minutes = {idle_timeout}

[tools]
# How to handle input/process tools:
#   "autonomous"          -- no prompts, full speed (default)
#   "confirm_destructive" -- prompt before kill_process / launch_app
#   "confirm_all"         -- prompt before every input/process tool call
# (Note: confirm_* are config-recognized but not yet implemented; they
#  currently fall back to autonomous and log a warning.)
input_auth_mode = "autonomous"

# When true, filesystem tools refuse paths outside `allow_roots`.
restrict_paths = false
allow_roots = []

# Comment out a tool name to disable it. The agent never sees it.
enabled = [
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
]

[logging]
audit_log_path = "audit.log"
level = "INFO"

[attach]
# Local-attach socket lets `python -m discord_claude_control.attach`
# connect from any local terminal and drive the same conversation Discord
# is driving. Loopback only.
enabled = {attach_enabled}
host    = "127.0.0.1"
port    = 9876
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discord-claude-control.setup",
        description="Interactive first-run config wizard.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing .env / config.toml",
    )
    parser.add_argument("--env-path", default=".env", help="path to write .env to")
    parser.add_argument("--config-path", default="config.toml", help="path to write config.toml to")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="write template files with placeholder values, no prompts",
    )
    args = parser.parse_args(argv)

    env_path = Path(args.env_path)
    config_path = Path(args.config_path)

    if not args.force and (env_path.exists() or config_path.exists()):
        print("Refusing to overwrite existing files:", file=sys.stderr)
        if env_path.exists():
            print(f"  {env_path}", file=sys.stderr)
        if config_path.exists():
            print(f"  {config_path}", file=sys.stderr)
        print("Pass --force to overwrite.", file=sys.stderr)
        return 1

    values = _placeholder_values() if args.non_interactive else _interactive_prompt()

    env_path.write_text(_ENV_TEMPLATE.format(**values["env"]), encoding="utf-8")
    config_path.write_text(_CONFIG_TEMPLATE.format(**values["config"]), encoding="utf-8")

    print()
    print(f"  wrote {env_path}")
    print(f"  wrote {config_path}")
    print()
    if not args.non_interactive:
        _print_next_steps(values)
    return 0


def _interactive_prompt() -> dict[str, dict[str, str]]:
    print()
    print("discord-claude-control setup")
    print("============================")
    print()
    print("This wizard collects everything the bot needs to start. All values")
    print("go into .env (secrets) and config.toml (the rest). Both files are")
    print("gitignored. You can edit them by hand later.")
    print()

    print("--- Step 1: Discord bot token ---")
    print("  1. https://discord.com/developers/applications  -> New Application")
    print("  2. Bot tab -> Reset Token -> copy.")
    print("  3. Privileged Gateway Intents -> enable 'Message Content Intent'.")
    print()
    bot_token = _prompt("Discord bot token", validator=_validate_token)

    print()
    print("--- Step 2: Discord IDs ---")
    print("  In Discord: Settings -> Advanced -> Developer Mode ON.")
    print("  Right-click your name / channel / server icon -> Copy ID.")
    print()
    user_id = _prompt("Your Discord user ID", validator=_validate_snowflake)
    channel_id = _prompt("Channel ID the bot listens in", validator=_validate_snowflake)
    guild_id = _prompt("Server (guild) ID", validator=_validate_snowflake)

    print()
    print("--- Step 3: Anthropic auth ---")
    print("  Pick one:")
    print("    A. API key (pay-per-token).  Set ANTHROPIC_API_KEY here.")
    print("    B. Pro/Max subscription.     Leave blank; run `claude /login` later.")
    print()
    api_key = _prompt(
        "Anthropic API key (paste, or press Enter for subscription mode)",
        default="",
        validator=None,
    )

    print()
    print("--- Step 4: a few optional knobs ---")
    model = _prompt("Claude model", default="claude-opus-4-7")
    idle_timeout = _prompt(
        "Idle timeout (minutes) before releasing the power request",
        default="10",
        validator=_validate_positive_int,
    )
    attach_enabled = _prompt_yes_no(
        "Enable local attach socket so `python -m discord_claude_control.attach` works?",
        default=True,
    )

    api_key_line = f"ANTHROPIC_API_KEY={api_key}" if api_key else "# ANTHROPIC_API_KEY="

    return {
        "env": {"bot_token": bot_token, "api_key_line": api_key_line},
        "config": {
            "user_id": user_id,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "model": model,
            "idle_timeout": idle_timeout,
            "attach_enabled": "true" if attach_enabled else "false",
        },
        "_meta": {"api_key_set": "1" if api_key else "0"},
    }


def _placeholder_values() -> dict[str, dict[str, str]]:
    return {
        "env": {
            "bot_token": "replace-with-your-discord-bot-token",
            "api_key_line": "# ANTHROPIC_API_KEY=sk-ant-...",
        },
        "config": {
            "user_id": "0",
            "channel_id": "0",
            "guild_id": "0",
            "model": "claude-opus-4-7",
            "idle_timeout": "10",
            "attach_enabled": "false",
        },
        "_meta": {"api_key_set": "0"},
    }


def _print_next_steps(values: dict[str, dict[str, str]]) -> None:
    print("Next steps:")
    step = 1
    if values["_meta"]["api_key_set"] == "0":
        print(
            f"  {step}. Run  claude /login  to authenticate Claude Code with your " "Pro/Max plan."
        )
        step += 1
    print(f"  {step}. Run  python -m discord_claude_control  to start the bot.")
    if values["config"]["attach_enabled"] == "true":
        step += 1
        print(f"  {step}. From any local terminal:  python -m discord_claude_control.attach")
    print()


def _prompt(
    label: str,
    *,
    default: str | None = None,
    validator: Callable[[str], None] | None = None,
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"  {label}{suffix}: ").strip()
        except EOFError:
            value = ""
        if not value:
            if default is not None:
                return default
            print("    (required, please enter a value)")
            continue
        if validator is not None:
            try:
                validator(value)
            except ValueError as e:
                print(f"    {e}")
                continue
        return value


def _prompt_yes_no(label: str, *, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"  {label} [{hint}]: ").strip().lower()
        except EOFError:
            raw = ""
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("    please answer y or n")


def _validate_token(value: str) -> None:
    if len(value) < 30:
        raise ValueError("that doesn't look like a Discord bot token (expected ~70 chars)")
    if " " in value:
        raise ValueError("bot tokens never contain spaces; check what you pasted")


def _validate_snowflake(value: str) -> None:
    if not value.isdigit():
        raise ValueError(
            "must be the numeric Discord ID (Developer Mode -> right-click -> Copy ID)"
        )
    if len(value) < 17:
        raise ValueError("Discord IDs are 17+ digits; that looks too short")


def _validate_positive_int(value: str) -> None:
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("must be a positive integer")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
