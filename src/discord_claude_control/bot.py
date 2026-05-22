from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import discord

from .agent import AgentSession
from .attach_server import AttachServer
from .audit import setup_audit_logger
from .auth import is_authorized_message
from .broker import AgentBroker
from .config import AgentConfig, Config, Secrets
from .discord_sink import DiscordResponseSink
from .health import format_status, run_heartbeat_loop
from .power import PowerRequest
from .session import SessionState
from .session_persist import SessionIdStore
from .system_prompt import DEFAULT_SYSTEM_PROMPT
from .tools import build_tools
from .tools._context import reset_channel, set_channel

log = logging.getLogger(__name__)


class DispatchBot(discord.Client):
    def __init__(self, config: Config, secrets: Secrets) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.config: Config = config
        self._secrets = secrets
        self._agent: AgentSession | None = None
        self._broker: AgentBroker | None = None
        self._attach: AttachServer | None = None
        self._session: SessionState | None = None
        self._session_stop: asyncio.Event | None = None
        self._session_task: asyncio.Task[None] | None = None
        self._heartbeat_stop: asyncio.Event | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._finalize_tasks: set[asyncio.Task[None]] = set()
        self._started_at: float = time.time()

    async def setup_hook(self) -> None:
        # The Claude Code CLI (which the SDK spawns) reads ANTHROPIC_API_KEY
        # from its environment if set. If not set, it falls back to whatever
        # auth `claude /login` configured (subscription Pro/Max). Either way
        # works; setdefault avoids stomping a real shell value if one is
        # present in os.environ.
        if self._secrets.anthropic_api_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", self._secrets.anthropic_api_key)
            log.info("authenticating via ANTHROPIC_API_KEY (pay-per-token)")
        else:
            log.info(
                "no ANTHROPIC_API_KEY set; expecting Claude Code to be "
                "authenticated via `claude /login` (subscription mode)"
            )

        setup_audit_logger(
            self.config.logging.audit_log_path,
            max_bytes=self.config.logging.audit_max_bytes,
            backup_count=self.config.logging.audit_backup_count,
        )
        store = SessionIdStore(Path(self.config.agent.conversation_db_path))
        mcp_server, allowed_tools = build_tools(self.config.tools)
        self._agent = AgentSession(
            config=self.config.agent,
            session_store=store,
            system_prompt=_resolve_system_prompt(self.config.agent),
            mcp_server=mcp_server,
            allowed_tools=allowed_tools,
        )
        await self._agent.connect()

        power = PowerRequest()
        self._session = SessionState(
            idle_timeout_seconds=self.config.session.idle_timeout_minutes * 60.0,
            power_request=power,
        )
        self._session_stop = asyncio.Event()
        self._session_task = asyncio.create_task(self._session.run_idle_loop(self._session_stop))

        self._broker = AgentBroker(self._agent)
        session = self._session
        self._broker.add_activity_listener(lambda reason: session.ping(reason))

        # Liveness heartbeat: a separate coroutine that touches logs/heartbeat
        # on a steady cadence. Independent of session activity so a wedged
        # event loop is visible to the external watchdog even when nothing
        # is being dispatched.
        self._heartbeat_stop = asyncio.Event()
        self._heartbeat_task = asyncio.create_task(run_heartbeat_loop(self._heartbeat_stop))

        if self.config.attach.enabled:
            self._attach = AttachServer(
                self._broker, self.config.attach.host, self.config.attach.port
            )
            await self._attach.start()

    async def close(self) -> None:
        if self._attach is not None:
            try:
                await self._attach.stop()
            except Exception:
                log.exception("attach server stop raised")
            self._attach = None
        if self._heartbeat_stop is not None:
            self._heartbeat_stop.set()
        if self._heartbeat_task is not None:
            try:
                await self._heartbeat_task
            except Exception:
                log.exception("heartbeat loop crashed on shutdown")
            self._heartbeat_task = None
        if self._session_stop is not None:
            self._session_stop.set()
        if self._session_task is not None:
            try:
                await self._session_task
            except Exception:
                log.exception("session idle loop crashed on shutdown")
            self._session_task = None
        if self._session is not None:
            self._session.force_release("shutdown")
        if self._agent is not None:
            try:
                await self._agent.disconnect()
            except Exception:
                log.exception("agent.disconnect raised; continuing shutdown")
        await super().close()

    async def on_ready(self) -> None:
        user = self.user
        if user is not None:
            log.info("connected as %s (id=%s)", user, user.id)

    async def on_connect(self) -> None:
        # Fired before identify completes. Useful to know the WebSocket
        # link is back even when on_ready is delayed by guild fill.
        log.info("gateway connected")

    async def on_disconnect(self) -> None:
        # discord.py reconnects automatically; this just makes the gap
        # visible in logs/stderr.log instead of being silent.
        log.warning("gateway disconnected")

    async def on_resumed(self) -> None:
        log.info("gateway session resumed")

    async def on_error(self, event_method: str, /, *args: object, **kwargs: object) -> None:
        # Default handler prints to stderr without a traceback header.
        # We want a clearly-tagged log line so the cause is greppable.
        log.exception("unhandled exception in event handler %s", event_method)

    async def on_message(self, message: discord.Message) -> None:
        self_user = self.user
        if self_user is not None and message.author.id == self_user.id:
            return
        if not is_authorized_message(message, self.config.discord):
            log.debug(
                "dropping unauthorized message from user=%s channel=%s guild=%s",
                message.author.id,
                message.channel.id,
                message.guild.id if message.guild else None,
            )
            return

        content = message.content
        if content == self.config.discord.stop_command:
            await self._handle_stop(message)
            return

        if content.strip().lower() == self.config.discord.ping_command.lower():
            await message.channel.send("pong")
            return

        if content.strip() == self.config.discord.status_command:
            await self._handle_status(message)
            return

        await self._dispatch_to_agent(message)

    async def _handle_stop(self, message: discord.Message) -> None:
        log.info("!stop received from user=%s", message.author.id)
        if self._broker is None or not self._broker.is_busy:
            await message.channel.send("nothing in flight.")
            return
        await self._broker.interrupt()
        await message.channel.send("stopped.")

    async def _handle_status(self, message: discord.Message) -> None:
        """Cheap liveness check that does NOT engage the agent."""
        broker_busy = bool(self._broker is not None and self._broker.is_busy)
        agent_connected = self._agent is not None
        session = self._session
        session_active = bool(session is not None and session.is_active)
        seconds_until_idle = session.seconds_until_idle if session is not None else None
        attach_clients = self._attach.client_count if self._attach is not None else None
        text = format_status(
            started_at=self._started_at,
            agent_connected=agent_connected,
            broker_busy=broker_busy,
            attach_clients=attach_clients,
            session_active=session_active,
            seconds_until_idle=seconds_until_idle,
        )
        await message.channel.send(f"```\n{text}\n```")

    async def _dispatch_to_agent(self, message: discord.Message) -> None:
        if self._broker is None:
            log.error("broker not initialized")
            await message.channel.send(":x: agent not ready; retry shortly")
            return
        if self._broker.is_busy:
            await message.channel.send(
                ":hourglass: still working on the previous message; use `!stop` to abort"
            )
            return

        sink = DiscordResponseSink(message.channel)
        token = set_channel(message.channel)

        async def _finalize() -> None:
            inflight = self._broker.inflight if self._broker else None
            if inflight is None:
                return
            try:
                await inflight
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("agent turn raised")
            finally:
                reset_channel(token)

        async with message.channel.typing():
            ok = self._broker.submit_nowait(message.content, "discord", extra_sinks=[sink])
            if not ok:
                await message.channel.send(
                    ":hourglass: still working on the previous message; use `!stop` to abort"
                )
                reset_channel(token)
                return
            task = asyncio.create_task(_finalize())
            self._finalize_tasks.add(task)
            task.add_done_callback(self._finalize_tasks.discard)


def _resolve_system_prompt(agent_cfg: AgentConfig) -> str:
    if agent_cfg.system_prompt is not None:
        return agent_cfg.system_prompt
    if agent_cfg.system_prompt_path is not None:
        path = Path(agent_cfg.system_prompt_path)
        if not path.exists():
            log.warning(
                "agent.system_prompt_path %s does not exist; falling back to default",
                path,
            )
            return DEFAULT_SYSTEM_PROMPT
        return path.read_text(encoding="utf-8")
    return DEFAULT_SYSTEM_PROMPT


def run_bot(config: Config, secrets: Secrets) -> None:
    bot = DispatchBot(config, secrets)
    bot.run(secrets.discord_bot_token, log_handler=None)
