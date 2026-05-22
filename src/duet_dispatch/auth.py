from __future__ import annotations

from typing import TYPE_CHECKING

from .config import DiscordConfig

if TYPE_CHECKING:  # pragma: no cover
    import discord


def is_authorized(
    *,
    user_id: int,
    channel_id: int,
    guild_id: int | None,
    config: DiscordConfig,
) -> bool:
    """All three IDs must match the configured allow-list. DMs (guild_id=None) are rejected."""
    if guild_id is None:
        return False
    return (
        user_id == config.allowed_user_id
        and channel_id == config.allowed_channel_id
        and guild_id == config.allowed_guild_id
    )


def is_authorized_message(message: discord.Message, config: DiscordConfig) -> bool:
    guild_id = message.guild.id if message.guild is not None else None
    return is_authorized(
        user_id=message.author.id,
        channel_id=message.channel.id,
        guild_id=guild_id,
        config=config,
    )
