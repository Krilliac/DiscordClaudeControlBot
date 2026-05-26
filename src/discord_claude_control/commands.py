"""
Declarative command catalog for the bang (message) and slash surfaces.

Every command listed here is shown in `!help` / `/help`. Handler wiring
lives in bot.py where the closures see the broker, agent, and session
state. Adding a new command means:

  1. add a CommandSpec row to COMMANDS below
  2. add the handler method to DispatchBot in bot.py
  3. wire it in DispatchBot._dispatch_command / the slash registration

The user-configurable bang strings (stop, ping, status, stay) carry their
defaults in DiscordConfig; resolve() folds the overrides in at startup so
the rest of the code sees a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class CommandSpec:
    name: str
    """Canonical short name. Used as the slash command name too."""

    bang: str | None
    """Exact-match string for the message surface (e.g. '!help', '!cost').
    None = no fixed default; the resolver supplies it from DiscordConfig
    (used for stop/ping/status/stay which are user-configurable)."""

    slash: bool
    """True = also registered as a guild-scoped Discord application command."""

    takes_args: bool
    """If True, message match is 'equals bang OR starts with bang + space',
    and the slash command accepts a single 'args' string parameter."""

    summary: str
    """One-line description shown in the !help table."""

    detail: str = ""
    """Multi-line detail shown by `!help <name>`. Empty = no detail beyond summary."""


@dataclass(frozen=True)
class ResolvedCommand:
    spec: CommandSpec
    bang: str | None


COMMANDS: Final[tuple[CommandSpec, ...]] = (
    CommandSpec(
        name="help",
        bang="!help",
        slash=True,
        takes_args=True,
        summary="list every command, or detail one",
        detail=(
            "!help          -- show all commands\n"
            "!help <name>   -- show detail for one command (e.g. !help cost)"
        ),
    ),
    CommandSpec(
        name="ping",
        bang=None,
        slash=False,
        takes_args=False,
        summary="cheapest liveness check (no LLM, no tokens)",
    ),
    CommandSpec(
        name="stop",
        bang=None,
        slash=True,
        takes_args=False,
        summary="interrupt the current agent turn",
        detail=(
            "If a turn is mid-stream, cancel it. If nothing is in flight, "
            "replies 'nothing in flight.'"
        ),
    ),
    CommandSpec(
        name="status",
        bang=None,
        slash=True,
        takes_args=False,
        summary="show bot uptime, idle/active state, attach client count",
    ),
    CommandSpec(
        name="stay",
        bang=None,
        slash=True,
        takes_args=False,
        summary="hold the PC awake for the configured idle window (no LLM)",
        detail=(
            "Pings the session, re-acquires the PowerRequest, and resets\n"
            "the idle countdown. Use this when a pre-sleep warning posts\n"
            "and you want to keep the bot reachable without spending tokens.\n"
            "Costs nothing -- intercepted before any agent dispatch."
        ),
    ),
    CommandSpec(
        name="cost",
        bang="!cost",
        slash=True,
        takes_args=True,
        summary="token + USD usage (today | week | month | all). default today.",
        detail=(
            "!cost              -- today's usage\n"
            "!cost today        -- same\n"
            "!cost week         -- last 7 days\n"
            "!cost month        -- last 30 days\n"
            "!cost all          -- since usage.log was created\n"
            "\n"
            "Subscription mode reports tokens only; total_cost_usd from the\n"
            "SDK is 0 in that case."
        ),
    ),
    CommandSpec(
        name="screenshot",
        bang=None,
        slash=True,
        takes_args=False,
        summary="grab a screenshot directly (no LLM turn, no tokens)",
        detail=(
            "Equivalent to telling the agent 'take a screenshot', but skips\n"
            "the model -- captures all monitors, posts the image to chat,\n"
            "and ends."
        ),
    ),
)


def resolve(
    commands: tuple[CommandSpec, ...] = COMMANDS,
    *,
    stop: str,
    ping: str,
    status: str,
    stay: str,
) -> tuple[ResolvedCommand, ...]:
    """Apply DiscordConfig overrides for the user-configurable bang strings."""
    overrides: dict[str, str] = {
        "stop": stop,
        "ping": ping,
        "status": status,
        "stay": stay,
    }
    return tuple(
        ResolvedCommand(spec=c, bang=overrides.get(c.name, c.bang)) for c in commands
    )


def find_by_bang(
    content: str, resolved: tuple[ResolvedCommand, ...]
) -> tuple[ResolvedCommand, str] | None:
    """Match `content` against the bang surface. Returns (cmd, args_tail) or None.

    The args_tail is whitespace-stripped. For commands with takes_args=False
    the tail is always the empty string (a non-empty tail makes the match fail).
    """
    for cmd in resolved:
        bang = cmd.bang
        if bang is None:
            continue
        stripped = content.strip()
        if cmd.spec.name == "ping":
            # Existing behavior: ping is case-insensitive.
            if stripped.lower() == bang.lower():
                return cmd, ""
            continue
        if cmd.spec.takes_args:
            if stripped == bang:
                return cmd, ""
            if stripped.startswith(bang + " "):
                return cmd, stripped[len(bang) + 1 :].strip()
            continue
        if stripped == bang:
            return cmd, ""
    return None


def render_overview(resolved: tuple[ResolvedCommand, ...]) -> str:
    """Render the !help / /help overview message."""
    lines: list[str] = ["**Commands**", ""]

    bang_rows: list[tuple[str, str]] = []
    for cmd in resolved:
        if cmd.bang is None:
            continue
        trigger = cmd.bang + (" <args>" if cmd.spec.takes_args else "")
        bang_rows.append((trigger, cmd.spec.summary))

    slash_rows: list[tuple[str, str]] = []
    for cmd in resolved:
        if not cmd.spec.slash:
            continue
        trigger = "/" + cmd.spec.name + (" <args>" if cmd.spec.takes_args else "")
        slash_rows.append((trigger, cmd.spec.summary))

    if bang_rows:
        lines.append("__Message commands:__")
        width = max(len(r[0]) for r in bang_rows)
        for trigger, summary in bang_rows:
            lines.append(f"`{trigger.ljust(width)}`  {summary}")
        lines.append("")

    if slash_rows:
        lines.append("__Slash commands:__")
        width = max(len(r[0]) for r in slash_rows)
        for trigger, summary in slash_rows:
            lines.append(f"`{trigger.ljust(width)}`  {summary}")
        lines.append("")

    lines.append("Any other message becomes a turn for Claude.")
    return "\n".join(lines)


def render_detail(name: str, resolved: tuple[ResolvedCommand, ...]) -> str | None:
    """Render `!help <name>` detail. Returns None if no such command."""
    for cmd in resolved:
        if cmd.spec.name != name:
            continue
        lines: list[str] = [f"**{cmd.spec.name}**", "", cmd.spec.summary, ""]
        if cmd.bang is not None:
            lines.append(f"message: `{cmd.bang}`")
        if cmd.spec.slash:
            lines.append(f"slash:   `/{cmd.spec.name}`")
        if cmd.spec.detail:
            lines.append("")
            lines.append(cmd.spec.detail)
        return "\n".join(lines).rstrip()
    return None
