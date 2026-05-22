"""screenshot tool: captures a monitor, posts it to Discord, returns both
an image content block (so Claude can see it) and a text summary."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from ._context import get_channel
from ._helpers import error_result

log = logging.getLogger(__name__)

_DESCRIPTION = (
    "Capture a screenshot of the user's screen. The image is automatically "
    "posted to the Discord channel AND returned to you so you can analyze "
    "what is currently on screen. monitor=0 is 'all monitors combined' "
    "(default); monitor=1 is the primary display."
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "monitor": {
            "type": "integer",
            "default": 0,
            "description": "0=all, 1=primary, 2+=secondary",
        },
    },
}


async def _screenshot(args: dict[str, Any]) -> dict[str, Any]:
    monitor = args.get("monitor", 0)
    if not isinstance(monitor, int) or isinstance(monitor, bool) or monitor < 0:
        return error_result("monitor must be a non-negative integer")

    try:
        import mss
        import mss.tools as mss_tools
    except ImportError as e:
        return error_result(f"missing screenshot dependency: {e}")

    try:
        with mss.mss() as sct:
            mons = sct.monitors
            if monitor >= len(mons):
                return error_result(
                    f"monitor {monitor} not available; have {len(mons) - 1} monitor(s)"
                )
            target = mons[monitor]
            sct_img = sct.grab(target)
            width, height = sct_img.size
            png_bytes_or_none = mss_tools.to_png(sct_img.rgb, sct_img.size)
            if not png_bytes_or_none:
                return error_result("mss.to_png returned no data")
            png_bytes: bytes = png_bytes_or_none
    except Exception as e:
        log.exception("screenshot capture failed")
        return error_result(f"screenshot capture failed: {e}")

    posted = False
    channel = get_channel()
    if channel is not None:
        try:
            import discord

            file = discord.File(io.BytesIO(png_bytes), filename=f"monitor-{monitor}.png")
            await channel.send(file=file)
            posted = True
        except Exception:
            log.exception("failed to post screenshot to Discord")

    b64 = base64.b64encode(png_bytes).decode("ascii")
    summary = f"captured monitor {monitor}: {width}x{height} ({len(png_bytes)} bytes)"
    if posted:
        summary += " -- posted to chat"
    else:
        summary += " -- no Discord channel context, not posted"
    return {
        "content": [
            {"type": "image", "data": b64, "mimeType": "image/png"},
            {"type": "text", "text": summary},
        ]
    }


def build_screenshot_tool() -> SdkMcpTool[Any]:
    return tool("screenshot", _DESCRIPTION, _SCHEMA)(_screenshot)
