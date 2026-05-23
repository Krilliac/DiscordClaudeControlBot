"""Tests for the new discord.status_command config field."""

from __future__ import annotations

from typing import Any

import pytest

from discord_claude_control.config import ConfigError, build_config


def _minimal_raw() -> dict[str, Any]:
    return {
        "discord": {
            "allowed_user_id": 111,
            "allowed_channel_id": 222,
            "allowed_guild_id": 333,
        }
    }


def test_default_status_command() -> None:
    cfg = build_config(_minimal_raw())
    assert cfg.discord.status_command == "!status"


def test_custom_status_command() -> None:
    raw = _minimal_raw()
    raw["discord"]["status_command"] = "!ping?"
    cfg = build_config(raw)
    assert cfg.discord.status_command == "!ping?"


def test_empty_status_command_rejected() -> None:
    raw = _minimal_raw()
    raw["discord"]["status_command"] = "   "
    with pytest.raises(ConfigError, match="status_command"):
        build_config(raw)


def test_status_must_differ_from_stop() -> None:
    raw = _minimal_raw()
    raw["discord"]["stop_command"] = "x"
    raw["discord"]["status_command"] = "x"
    with pytest.raises(ConfigError, match="differ"):
        build_config(raw)


def test_status_must_differ_from_ping() -> None:
    raw = _minimal_raw()
    raw["discord"]["ping_command"] = "ping"
    raw["discord"]["status_command"] = "ping"
    with pytest.raises(ConfigError, match="differ"):
        build_config(raw)


def test_all_three_distinct_accepted() -> None:
    raw = _minimal_raw()
    raw["discord"]["stop_command"] = "!halt"
    raw["discord"]["ping_command"] = "alive"
    raw["discord"]["status_command"] = "?how"
    cfg = build_config(raw)
    assert cfg.discord.stop_command == "!halt"
    assert cfg.discord.ping_command == "alive"
    assert cfg.discord.status_command == "?how"
