"""
Reaction-based confirmation prompt for destructive tools.

`request_confirmation` posts a message into the active Discord channel,
adds ✅ and ❌ reactions, and waits for the configured user to react.

Default-deny policy: if there is no live channel + bot pair (e.g. an
attach-driven turn or a unit test without mocks), the call returns
False rather than letting a destructive operation proceed silently.
"""

from __future__ import annotations

import asyncio
import logging

from ._context import get_bot, get_channel

log = logging.getLogger(__name__)

YES_EMOJI = "✅"  # ✅
NO_EMOJI = "❌"  # ❌

DEFAULT_TIMEOUT_S = 30.0


async def request_confirmation(
    *,
    user_id: int,
    action: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> bool:
    """Return True if the configured user reacted with ✅ within timeout.
    False on ❌, on timeout, or if no interactive context is available."""
    channel = get_channel()
    bot = get_bot()
    if channel is None or bot is None:
        log.warning("confirm: no channel/bot context; denying %r", action)
        return False

    try:
        msg = await channel.send(
            f":warning: confirm: **{action}**\n"
            f"react {YES_EMOJI} within {timeout_s:.0f}s to approve, "
            f"{NO_EMOJI} to deny."
        )
        await msg.add_reaction(YES_EMOJI)
        await msg.add_reaction(NO_EMOJI)
    except Exception:
        log.exception("confirm: failed to post prompt")
        return False

    def _check(reaction: object, user: object) -> bool:
        user_match = getattr(user, "id", None) == user_id
        if not user_match:
            return False
        emoji_str = str(getattr(reaction, "emoji", ""))
        msg_id = getattr(getattr(reaction, "message", None), "id", None)
        return msg_id == msg.id and emoji_str in (YES_EMOJI, NO_EMOJI)

    try:
        reaction, _user = await bot.wait_for(
            "reaction_add", check=_check, timeout=timeout_s
        )
    except asyncio.TimeoutError:
        try:
            await msg.edit(content=f":hourglass: timed out: **{action}**")
        except Exception:
            log.debug("confirm: timeout edit failed", exc_info=True)
        return False
    except Exception:
        log.exception("confirm: wait_for raised")
        return False

    approved = str(getattr(reaction, "emoji", "")) == YES_EMOJI
    try:
        marker = "approved" if approved else "denied"
        emoji = YES_EMOJI if approved else NO_EMOJI
        await msg.edit(content=f"{emoji} {marker}: **{action}**")
    except Exception:
        log.debug("confirm: result edit failed", exc_info=True)
    return approved


def describe_action(tool_name: str, args: dict) -> str:
    """Render tool args into a short human-readable line for the prompt."""
    if tool_name == "kill_process":
        return f"kill_process({args.get('name_or_pid')!r})"
    if tool_name == "launch_app":
        path = args.get("path")
        extras = args.get("args") or []
        if extras:
            return f"launch_app({path!r}, args={extras!r})"
        return f"launch_app({path!r})"
    if tool_name == "click":
        return f"click({args.get('button', 'left')!r})"
    if tool_name == "type_text":
        text = str(args.get("text", ""))
        snippet = text if len(text) <= 60 else text[:60] + "..."
        return f"type_text({snippet!r})"
    if tool_name == "press_key":
        return f"press_key({args.get('key')!r})"
    if tool_name == "move_mouse":
        return f"move_mouse({args.get('x')}, {args.get('y')})"
    return f"{tool_name}({args!r})"
