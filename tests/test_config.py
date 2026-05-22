from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from discord_claude_control.config import ConfigError, build_config, load_config


def _minimal_raw() -> dict[str, Any]:
    return {
        "discord": {
            "allowed_user_id": 111,
            "allowed_channel_id": 222,
            "allowed_guild_id": 333,
        }
    }


def test_minimal_config_loads_with_defaults() -> None:
    cfg = build_config(_minimal_raw())
    assert cfg.discord.allowed_user_id == 111
    assert cfg.discord.allowed_channel_id == 222
    assert cfg.discord.allowed_guild_id == 333
    assert cfg.agent.model == "claude-opus-4-7"
    assert cfg.agent.max_tool_calls_per_message == 20
    assert cfg.session.idle_timeout_minutes == 10
    assert cfg.tools.input_auth_mode == "autonomous"
    assert cfg.tools.restrict_paths is False
    assert cfg.tools.allow_roots == ()
    assert cfg.tools.enabled == ()
    assert cfg.logging.level == "INFO"
    assert cfg.logging.audit_log_path == "audit.log"
    assert cfg.agent.system_prompt is None
    assert cfg.agent.system_prompt_path is None
    assert cfg.attach.enabled is False
    assert cfg.attach.host == "127.0.0.1"
    assert cfg.attach.port == 9876


def test_system_prompt_inline_set() -> None:
    raw = _minimal_raw()
    raw["agent"] = {"system_prompt": "You are a teapot."}
    cfg = build_config(raw)
    assert cfg.agent.system_prompt == "You are a teapot."
    assert cfg.agent.system_prompt_path is None


def test_system_prompt_path_set() -> None:
    raw = _minimal_raw()
    raw["agent"] = {"system_prompt_path": "prompts/custom.txt"}
    cfg = build_config(raw)
    assert cfg.agent.system_prompt is None
    assert cfg.agent.system_prompt_path == "prompts/custom.txt"


def test_system_prompt_inline_and_path_rejected() -> None:
    raw = _minimal_raw()
    raw["agent"] = {"system_prompt": "x", "system_prompt_path": "y.txt"}
    with pytest.raises(ConfigError, match="mutually exclusive"):
        build_config(raw)


def test_attach_enabled_with_valid_port() -> None:
    raw = _minimal_raw()
    raw["attach"] = {"enabled": True, "port": 12345}
    cfg = build_config(raw)
    assert cfg.attach.enabled is True
    assert cfg.attach.port == 12345


def test_attach_enabled_invalid_port_rejected() -> None:
    raw = _minimal_raw()
    raw["attach"] = {"enabled": True, "port": 70000}
    with pytest.raises(ConfigError, match=r"attach\.port"):
        build_config(raw)


def test_default_stop_and_ping_commands() -> None:
    cfg = build_config(_minimal_raw())
    assert cfg.discord.stop_command == "!stop"
    assert cfg.discord.ping_command == "ping"


def test_custom_stop_and_ping_commands() -> None:
    raw = _minimal_raw()
    raw["discord"]["stop_command"] = "!halt"
    raw["discord"]["ping_command"] = "alive"
    cfg = build_config(raw)
    assert cfg.discord.stop_command == "!halt"
    assert cfg.discord.ping_command == "alive"


def test_empty_stop_command_rejected() -> None:
    raw = _minimal_raw()
    raw["discord"]["stop_command"] = "   "
    with pytest.raises(ConfigError, match="stop_command"):
        build_config(raw)


def test_stop_and_ping_must_differ() -> None:
    raw = _minimal_raw()
    raw["discord"]["stop_command"] = "halt"
    raw["discord"]["ping_command"] = "halt"
    with pytest.raises(ConfigError, match="differ"):
        build_config(raw)


def test_default_tool_truncate_and_timeout() -> None:
    cfg = build_config(_minimal_raw())
    assert cfg.tools.output_truncate_at == 1500
    assert cfg.tools.powershell_default_timeout_s == 30


def test_custom_tool_truncate_and_timeout() -> None:
    raw = _minimal_raw()
    raw["tools"] = {"output_truncate_at": 9999, "powershell_default_timeout_s": 120}
    cfg = build_config(raw)
    assert cfg.tools.output_truncate_at == 9999
    assert cfg.tools.powershell_default_timeout_s == 120


def test_non_positive_tool_truncate_rejected() -> None:
    raw = _minimal_raw()
    raw["tools"] = {"output_truncate_at": 0}
    with pytest.raises(ConfigError, match="output_truncate_at"):
        build_config(raw)


def test_default_audit_rotation_settings() -> None:
    cfg = build_config(_minimal_raw())
    assert cfg.logging.audit_max_bytes == 5 * 1024 * 1024
    assert cfg.logging.audit_backup_count == 5


def test_custom_audit_rotation_settings() -> None:
    raw = _minimal_raw()
    raw["logging"] = {"audit_max_bytes": 1024, "audit_backup_count": 0}
    cfg = build_config(raw)
    assert cfg.logging.audit_max_bytes == 1024
    assert cfg.logging.audit_backup_count == 0


def test_invalid_audit_max_bytes_rejected() -> None:
    raw = _minimal_raw()
    raw["logging"] = {"audit_max_bytes": 0}
    with pytest.raises(ConfigError, match="audit_max_bytes"):
        build_config(raw)


def test_negative_audit_backup_count_rejected() -> None:
    raw = _minimal_raw()
    raw["logging"] = {"audit_backup_count": -1}
    with pytest.raises(ConfigError, match="audit_backup_count"):
        build_config(raw)


def test_missing_discord_section_rejected() -> None:
    with pytest.raises(ConfigError, match="discord"):
        build_config({})


def test_zero_user_id_rejected() -> None:
    raw = _minimal_raw()
    raw["discord"]["allowed_user_id"] = 0
    with pytest.raises(ConfigError, match="allowed_user_id"):
        build_config(raw)


def test_invalid_input_auth_mode_rejected() -> None:
    raw = _minimal_raw()
    raw["tools"] = {"input_auth_mode": "yolo"}
    with pytest.raises(ConfigError, match="input_auth_mode"):
        build_config(raw)


def test_all_input_auth_modes_accepted() -> None:
    for mode in ("autonomous", "confirm_destructive", "confirm_all"):
        raw = _minimal_raw()
        raw["tools"] = {"input_auth_mode": mode}
        cfg = build_config(raw)
        assert cfg.tools.input_auth_mode == mode


def test_custom_idle_timeout() -> None:
    raw = _minimal_raw()
    raw["session"] = {"idle_timeout_minutes": 42}
    cfg = build_config(raw)
    assert cfg.session.idle_timeout_minutes == 42


def test_non_positive_idle_timeout_rejected() -> None:
    raw = _minimal_raw()
    raw["session"] = {"idle_timeout_minutes": 0}
    with pytest.raises(ConfigError, match="idle_timeout_minutes"):
        build_config(raw)


def test_bool_rejected_for_int_field() -> None:
    raw = _minimal_raw()
    raw["agent"] = {"max_tool_calls_per_message": True}
    with pytest.raises(ConfigError, match="max_tool_calls_per_message"):
        build_config(raw)


def test_wrong_section_type_rejected() -> None:
    raw: dict[str, Any] = {"discord": "not a table"}
    with pytest.raises(ConfigError, match="discord"):
        build_config(raw)


def test_allow_roots_list_of_strings() -> None:
    raw = _minimal_raw()
    raw["tools"] = {"allow_roots": ["C:/Users/me", "D:/work"]}
    cfg = build_config(raw)
    assert cfg.tools.allow_roots == ("C:/Users/me", "D:/work")


def test_allow_roots_rejects_non_string_entries() -> None:
    raw = _minimal_raw()
    raw["tools"] = {"allow_roots": ["ok", 42]}
    with pytest.raises(ConfigError, match="allow_roots"):
        build_config(raw)


def test_logging_level_normalized_to_upper() -> None:
    raw = _minimal_raw()
    raw["logging"] = {"level": "debug"}
    cfg = build_config(raw)
    assert cfg.logging.level == "DEBUG"


def test_load_config_from_disk(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
[discord]
allowed_user_id = 111
allowed_channel_id = 222
allowed_guild_id = 333

[session]
idle_timeout_minutes = 5
""".strip())
    cfg = load_config(cfg_path)
    assert cfg.discord.allowed_user_id == 111
    assert cfg.session.idle_timeout_minutes == 5


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.toml")
