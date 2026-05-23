from __future__ import annotations

from discord_claude_control.auth import is_authorized
from discord_claude_control.config import DiscordConfig

CFG = DiscordConfig(
    allowed_user_id=111,
    allowed_channel_id=222,
    allowed_guild_id=333,
    stop_command="!stop",
    ping_command="ping",
    status_command="!status",
)


def test_exact_match_authorizes() -> None:
    assert is_authorized(user_id=111, channel_id=222, guild_id=333, config=CFG) is True


def test_wrong_user_rejected() -> None:
    assert is_authorized(user_id=999, channel_id=222, guild_id=333, config=CFG) is False


def test_wrong_channel_rejected() -> None:
    assert is_authorized(user_id=111, channel_id=999, guild_id=333, config=CFG) is False


def test_wrong_guild_rejected() -> None:
    assert is_authorized(user_id=111, channel_id=222, guild_id=999, config=CFG) is False


def test_dm_no_guild_rejected() -> None:
    assert is_authorized(user_id=111, channel_id=222, guild_id=None, config=CFG) is False


def test_all_zero_rejected() -> None:
    assert is_authorized(user_id=0, channel_id=0, guild_id=0, config=CFG) is False


def test_off_by_one_rejected() -> None:
    assert is_authorized(user_id=112, channel_id=222, guild_id=333, config=CFG) is False
    assert is_authorized(user_id=111, channel_id=223, guild_id=333, config=CFG) is False
    assert is_authorized(user_id=111, channel_id=222, guild_id=334, config=CFG) is False
