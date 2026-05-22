from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import discord

from .agent import AgentSession
from .attach_server import AttachServer
from .audit import setup_audit_logger
from .auth import is_authorized_message
from .broker import AgentBroker
from .config import AgentConfig, Config, Secrets
from .discord_sink import DiscordResponseSink
from .power import PowerRequest
from .session import SessionState
from .session_persist import SessionIdStore
from .system_prompt import DEFAULT_SYSTEM_PROMPT
from .tools import build_tools
from .tools._context import reset_channel, set_channel

log = logging.getLogger(__name__)

STOP_COMMAND = "!stop"
PING_COMMAND = "ping"


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
        self._finalize_tasks: set[asyncio.Task[None]] = set()

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

        setup_audit_logger(self.config.logging.audit_log_path)
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

        if message.content == STOP_COMMAND:
            await self._handle_stop(message)
            return

        if message.content.strip().lower() == PING_COMMAND:
            await message.channel.send("pong")
            return

        await self._dispatch_to_agent(message)

    async def _handle_stop(self, message: discord.Message) -> None:
        log.info("!stop received from user=%s", message.author.id)
        if self._broker is None or not self._broker.is_busy:
            await message.channel.send("nothing in flight.")
            return
        await self._broker.interrupt()
        await message.channel.send("stopped.")

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
