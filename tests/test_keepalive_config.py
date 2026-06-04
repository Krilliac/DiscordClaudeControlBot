"""Tests for the new keepalive config section and stay_command field."""

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


# ---- stay_command --------------------------------------------------------


def test_default_stay_command() -> None:
    cfg = build_config(_minimal_raw())
    assert cfg.discord.stay_command == "!stay"


def test_custom_stay_command() -> None:
    raw = _minimal_raw()
    raw["discord"]["stay_command"] = "!awake"
    cfg = build_config(raw)
    assert cfg.discord.stay_command == "!awake"


def test_empty_stay_command_rejected() -> None:
    raw = _minimal_raw()
    raw["discord"]["stay_command"] = "  "
    with pytest.raises(ConfigError, match="stay_command"):
        build_config(raw)


def test_stay_must_differ_from_other_commands() -> None:
    raw = _minimal_raw()
    raw["discord"]["stay_command"] = "!stop"  # collides with default stop_command
    with pytest.raises(ConfigError, match="differ"):
        build_config(raw)
    raw["discord"]["stay_command"] = "ping"  # collides with default ping_command
    with pytest.raises(ConfigError, match="differ"):
        build_config(raw)
    raw["discord"]["stay_command"] = "!status"  # collides with default status_command
    with pytest.raises(ConfigError, match="differ"):
        build_config(raw)


def test_all_four_distinct_accepted() -> None:
    raw = _minimal_raw()
    raw["discord"]["stop_command"] = "a"
    raw["discord"]["ping_command"] = "b"
    raw["discord"]["status_command"] = "c"
    raw["discord"]["stay_command"] = "d"
    cfg = build_config(raw)
    assert (
        cfg.discord.stop_command,
        cfg.discord.ping_command,
        cfg.discord.status_command,
        cfg.discord.stay_command,
    ) == ("a", "b", "c", "d")


# ---- [keepalive] section ------------------------------------------------


def test_keepalive_defaults() -> None:
    cfg = build_config(_minimal_raw())
    assert cfg.keepalive.enabled is True
    assert cfg.keepalive.warn_at_minutes == (60, 30, 15, 5)
    assert cfg.keepalive.warn_on_battery is False
    assert cfg.keepalive.poll_interval_seconds == 30


def test_keepalive_custom_thresholds_normalized_sorted_dedup() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"warn_at_minutes": [5, 5, 10, 60]}
    cfg = build_config(raw)
    # Sorted descending, deduped
    assert cfg.keepalive.warn_at_minutes == (60, 10, 5)


def test_keepalive_disabled() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"enabled": False}
    cfg = build_config(raw)
    assert cfg.keepalive.enabled is False


def test_keepalive_warn_on_battery_true() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"warn_on_battery": True}
    cfg = build_config(raw)
    assert cfg.keepalive.warn_on_battery is True


def test_keepalive_zero_threshold_rejected() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"warn_at_minutes": [0, 5]}
    with pytest.raises(ConfigError, match="warn_at_minutes"):
        build_config(raw)


def test_keepalive_negative_threshold_rejected() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"warn_at_minutes": [-5, 5]}
    with pytest.raises(ConfigError, match="warn_at_minutes"):
        build_config(raw)


def test_keepalive_non_int_threshold_rejected() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"warn_at_minutes": ["five"]}
    with pytest.raises(ConfigError, match="warn_at_minutes"):
        build_config(raw)


def test_keepalive_non_list_threshold_rejected() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"warn_at_minutes": 5}
    with pytest.raises(ConfigError, match="warn_at_minutes"):
        build_config(raw)


def test_keepalive_zero_poll_interval_rejected() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"poll_interval_seconds": 0}
    with pytest.raises(ConfigError, match="poll_interval_seconds"):
        build_config(raw)


def test_keepalive_custom_poll_interval() -> None:
    raw = _minimal_raw()
    raw["keepalive"] = {"poll_interval_seconds": 60}
    cfg = build_config(raw)
    assert cfg.keepalive.poll_interval_seconds == 60
