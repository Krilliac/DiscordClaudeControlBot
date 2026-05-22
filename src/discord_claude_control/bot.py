from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import discord

from .agent import AgentSession
from .audit import setup_audit_logger
from .auth import is_authorized_message
from .config import Config, Secrets
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
        self._inflight: asyncio.Task[None] | None = None
        self._session: SessionState | None = None
        self._session_stop: asyncio.Event | None = None
        self._session_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        # The SDK spawns the Claude Code CLI, which reads ANTHROPIC_API_KEY
        # from its environment. setdefault avoids stomping a real shell value.
        os.environ.setdefault("ANTHROPIC_API_KEY", self._secrets.anthropic_api_key)
        setup_audit_logger(self.config.logging.audit_log_path)
        store = SessionIdStore(Path(self.config.agent.conversation_db_path))
        mcp_server, allowed_tools = build_tools(self.config.tools)
        self._agent = AgentSession(
            config=self.config.agent,
            session_store=store,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
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

    async def close(self) -> None:
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

        # !stop is matched verbatim before anything else so a wedged agent
        # can always be killed.
        if message.content == STOP_COMMAND:
            await self._handle_stop(message)
            return

        # Cheap liveness check; does not spend tokens.
        if message.content.strip().lower() == PING_COMMAND:
            await message.channel.send("pong")
            return

        await self._dispatch_to_agent(message)

    async def _handle_stop(self, message: discord.Message) -> None:
        log.info("!stop received from user=%s", message.author.id)
        if self._inflight is None or self._inflight.done():
            await message.channel.send("nothing in flight.")
            return
        if self._agent is not None:
            try:
                await self._agent.interrupt()
            except Exception:
                log.exception("agent.interrupt raised; cancelling task anyway")
        self._inflight.cancel()
        await message.channel.send("stopped.")

    async def _dispatch_to_agent(self, message: discord.Message) -> None:
        if self._agent is None:
            log.error("agent not initialized")
            await message.channel.send(":x: agent not ready; retry shortly")
            return
        if self._inflight is not None and not self._inflight.done():
            await message.channel.send(
                ":hourglass: still working on the previous message; use `!stop` to abort"
            )
            return

        if self._session is not None:
            self._session.ping("user message")

        sink = DiscordResponseSink(message.channel)
        agent = self._agent
        session = self._session

        async def _run() -> None:
            token = set_channel(message.channel)
            try:
                async with message.channel.typing():
                    await agent.submit(message.content, sink)
            except asyncio.CancelledError:
                log.info("agent task cancelled by !stop")
                raise
            except Exception:
                log.exception("agent task crashed")
            finally:
                reset_channel(token)
                if session is not None:
                    session.ping("turn complete")

        self._inflight = asyncio.create_task(_run())


def run_bot(config: Config, secrets: Secrets) -> None:
    bot = DispatchBot(config, secrets)
    bot.run(secrets.discord_bot_token, log_handler=None)
