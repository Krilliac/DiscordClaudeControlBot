from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from dotenv import load_dotenv

InputAuthMode = Literal["autonomous", "confirm_destructive", "confirm_all"]
_INPUT_AUTH_MODES: tuple[str, ...] = get_args(InputAuthMode)


@dataclass(frozen=True)
class DiscordConfig:
    allowed_user_id: int
    allowed_channel_id: int
    allowed_guild_id: int
    stop_command: str
    ping_command: str
    status_command: str
    stay_command: str


@dataclass(frozen=True)
class AgentConfig:
    model: str
    max_tool_calls_per_message: int
    conversation_db_path: str
    system_prompt: str | None
    system_prompt_path: str | None


@dataclass(frozen=True)
class SessionConfig:
    idle_timeout_minutes: int


@dataclass(frozen=True)
class ToolsConfig:
    input_auth_mode: InputAuthMode
    restrict_paths: bool
    allow_roots: tuple[str, ...]
    enabled: tuple[str, ...]
    output_truncate_at: int
    powershell_default_timeout_s: int


@dataclass(frozen=True)
class LoggingConfig:
    audit_log_path: str
    usage_log_path: str
    level: str
    audit_max_bytes: int
    audit_backup_count: int


@dataclass(frozen=True)
class AttachConfig:
    enabled: bool
    host: str
    port: int


@dataclass(frozen=True)
class KeepaliveConfig:
    """Pre-sleep warning thresholds and cadence. Token-free notifications."""

    enabled: bool
    warn_at_minutes: tuple[int, ...]
    warn_on_battery: bool
    poll_interval_seconds: int


@dataclass(frozen=True)
class Config:
    discord: DiscordConfig
    agent: AgentConfig
    session: SessionConfig
    tools: ToolsConfig
    logging: LoggingConfig
    attach: AttachConfig
    keepalive: KeepaliveConfig


@dataclass(frozen=True)
class Secrets:
    anthropic_api_key: str | None
    discord_bot_token: str

    @property
    def has_api_key(self) -> bool:
        return bool(self.anthropic_api_key)


class ConfigError(ValueError):
    """Raised when config.toml is malformed or missing required values."""


def load_config(config_path: Path | str = "config.toml") -> Config:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"config.toml not found at {path}")
    with path.open("rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    return build_config(raw)


def load_secrets() -> Secrets:
    load_dotenv()
    return Secrets(
        anthropic_api_key=_optional_env("ANTHROPIC_API_KEY"),
        discord_bot_token=_require_env("DISCORD_BOT_TOKEN"),
    )


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value if value else None


def build_config(raw: dict[str, Any]) -> Config:
    discord = _section(raw, "discord", required=True)
    agent = _section(raw, "agent")
    session = _section(raw, "session")
    tools = _section(raw, "tools")
    logging_ = _section(raw, "logging")
    attach = _section(raw, "attach")
    keepalive = _section(raw, "keepalive")

    discord_cfg = DiscordConfig(
        allowed_user_id=_int(discord, "allowed_user_id"),
        allowed_channel_id=_int(discord, "allowed_channel_id"),
        allowed_guild_id=_int(discord, "allowed_guild_id"),
        stop_command=_str(discord, "stop_command", default="!stop"),
        ping_command=_str(discord, "ping_command", default="ping"),
        status_command=_str(discord, "status_command", default="!status"),
        stay_command=_str(discord, "stay_command", default="!stay"),
    )
    for name in ("allowed_user_id", "allowed_channel_id", "allowed_guild_id"):
        if getattr(discord_cfg, name) <= 0:
            raise ConfigError(f"discord.{name} must be a positive Discord snowflake ID")
    for cmd_field in ("stop_command", "ping_command", "status_command", "stay_command"):
        if not getattr(discord_cfg, cmd_field).strip():
            raise ConfigError(f"discord.{cmd_field} must be non-empty")
    distinct = {
        discord_cfg.stop_command,
        discord_cfg.ping_command,
        discord_cfg.status_command,
        discord_cfg.stay_command,
    }
    if len(distinct) != 4:
        raise ConfigError(
            "discord.stop_command, ping_command, status_command, and stay_command must all differ"
        )

    agent_cfg = AgentConfig(
        model=_str(agent, "model", default="claude-opus-4-7"),
        max_tool_calls_per_message=_int(agent, "max_tool_calls_per_message", default=20),
        conversation_db_path=_str(agent, "conversation_db_path", default="conversation.db"),
        system_prompt=_optional_str(agent, "system_prompt"),
        system_prompt_path=_optional_str(agent, "system_prompt_path"),
    )
    if agent_cfg.max_tool_calls_per_message <= 0:
        raise ConfigError("agent.max_tool_calls_per_message must be > 0")
    if agent_cfg.system_prompt is not None and agent_cfg.system_prompt_path is not None:
        raise ConfigError("agent.system_prompt and agent.system_prompt_path are mutually exclusive")

    session_cfg = SessionConfig(
        idle_timeout_minutes=_int(session, "idle_timeout_minutes", default=10),
    )
    if session_cfg.idle_timeout_minutes <= 0:
        raise ConfigError("session.idle_timeout_minutes must be > 0")

    mode_str = _str(tools, "input_auth_mode", default="autonomous")
    if mode_str not in _INPUT_AUTH_MODES:
        raise ConfigError(
            f"tools.input_auth_mode must be one of {_INPUT_AUTH_MODES}, got {mode_str!r}"
        )

    tools_cfg = ToolsConfig(
        input_auth_mode=mode_str,  # type: ignore[arg-type]
        restrict_paths=_bool(tools, "restrict_paths", default=False),
        allow_roots=tuple(_str_list(tools, "allow_roots", default=[])),
        enabled=tuple(_str_list(tools, "enabled", default=[])),
        output_truncate_at=_int(tools, "output_truncate_at", default=1500),
        powershell_default_timeout_s=_int(tools, "powershell_default_timeout_s", default=30),
    )
    if tools_cfg.output_truncate_at <= 0:
        raise ConfigError("tools.output_truncate_at must be > 0")
    if tools_cfg.powershell_default_timeout_s <= 0:
        raise ConfigError("tools.powershell_default_timeout_s must be > 0")

    logging_cfg = LoggingConfig(
        audit_log_path=_str(logging_, "audit_log_path", default="audit.log"),
        usage_log_path=_str(logging_, "usage_log_path", default="usage.log"),
        level=_str(logging_, "level", default="INFO").upper(),
        audit_max_bytes=_int(logging_, "audit_max_bytes", default=5 * 1024 * 1024),
        audit_backup_count=_int(logging_, "audit_backup_count", default=5),
    )
    if logging_cfg.audit_max_bytes <= 0:
        raise ConfigError("logging.audit_max_bytes must be > 0")
    if logging_cfg.audit_backup_count < 0:
        raise ConfigError("logging.audit_backup_count must be >= 0")
    if not logging_cfg.usage_log_path.strip():
        raise ConfigError("logging.usage_log_path must be non-empty")

    attach_cfg = AttachConfig(
        enabled=_bool(attach, "enabled", default=False),
        host=_str(attach, "host", default="127.0.0.1"),
        port=_int(attach, "port", default=9876),
    )
    if attach_cfg.enabled and not (1 <= attach_cfg.port <= 65535):
        raise ConfigError("attach.port must be in [1, 65535]")

    warn_minutes = _int_list(keepalive, "warn_at_minutes", default=[60, 30, 15, 5])
    keepalive_cfg = KeepaliveConfig(
        enabled=_bool(keepalive, "enabled", default=True),
        warn_at_minutes=tuple(sorted(set(warn_minutes), reverse=True)),
        warn_on_battery=_bool(keepalive, "warn_on_battery", default=False),
        poll_interval_seconds=_int(keepalive, "poll_interval_seconds", default=30),
    )
    if any(m <= 0 for m in keepalive_cfg.warn_at_minutes):
        raise ConfigError("keepalive.warn_at_minutes entries must all be > 0")
    if keepalive_cfg.poll_interval_seconds <= 0:
        raise ConfigError("keepalive.poll_interval_seconds must be > 0")

    return Config(
        discord=discord_cfg,
        agent=agent_cfg,
        session=session_cfg,
        tools=tools_cfg,
        logging=logging_cfg,
        attach=attach_cfg,
        keepalive=keepalive_cfg,
    )


def _section(raw: dict[str, Any], name: str, *, required: bool = False) -> dict[str, Any]:
    value = raw.get(name)
    if value is None:
        if required:
            raise ConfigError(f"config: missing required section [{name}]")
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"config: section [{name}] must be a table")
    return value


_MISSING: Any = object()


def _int(section: dict[str, Any], key: str, *, default: int = _MISSING) -> int:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        if default is _MISSING:
            raise ConfigError(f"config: missing required int {key}")
        return default
    # bool is a subclass of int; reject it explicitly so true/false isn't accepted as 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"config: {key} must be an integer")
    return value


def _str(section: dict[str, Any], key: str, *, default: str = _MISSING) -> str:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        if default is _MISSING:
            raise ConfigError(f"config: missing required string {key}")
        return default
    if not isinstance(value, str):
        raise ConfigError(f"config: {key} must be a string")
    return value


def _optional_str(section: dict[str, Any], key: str) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"config: {key} must be a string")
    return value if value else None


def _bool(section: dict[str, Any], key: str, *, default: bool) -> bool:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"config: {key} must be a boolean")
    return value


def _str_list(section: dict[str, Any], key: str, *, default: list[str]) -> list[str]:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        return list(default)
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"config: {key} must be a list of strings")
    return list(value)


def _int_list(section: dict[str, Any], key: str, *, default: list[int]) -> list[int]:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        return list(default)
    if not isinstance(value, list):
        raise ConfigError(f"config: {key} must be a list of integers")
    for x in value:
        if isinstance(x, bool) or not isinstance(x, int):
            raise ConfigError(f"config: {key} entries must be integers")
    return list(value)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"required env var {name} is missing or empty (set it in .env)")
    return value
