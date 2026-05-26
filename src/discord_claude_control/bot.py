from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands

from . import usage
from .agent import AgentSession, ImageBlob, UserTurn
from .attach_server import AttachServer
from .audit import setup_audit_logger
from .auth import is_authorized, is_authorized_message
from .broker import AgentBroker
from .commands import (
    ResolvedCommand,
    find_by_bang,
    render_detail,
    render_overview,
    resolve,
)
from .config import AgentConfig, Config, Secrets
from .discord_sink import DiscordResponseSink
from .health import format_status, run_heartbeat_loop
from .keepalive import KeepAlive
from .power import PowerRequest
from .session import SessionState
from .session_persist import SessionIdStore
from .system_prompt import DEFAULT_SYSTEM_PROMPT
from .tools import build_tools
from .tools._context import reset_channel, set_bot, set_channel

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
        self._keepalive_stop: asyncio.Event | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._finalize_tasks: set[asyncio.Task[None]] = set()
        self._started_at: float = time.time()
        self._commands: tuple[ResolvedCommand, ...] = resolve(
            stop=config.discord.stop_command,
            ping=config.discord.ping_command,
            status=config.discord.status_command,
            stay=config.discord.stay_command,
        )
        self._usage_log_path = Path(config.logging.usage_log_path)
        self.tree = app_commands.CommandTree(self)
        self._register_slash_handlers()

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
        # Make the bot client visible to tools that need to wait_for a
        # reaction (confirmation prompts). Set once; never reset.
        set_bot(self)

        store = SessionIdStore(Path(self.config.agent.conversation_db_path))
        mcp_server, allowed_tools = build_tools(
            self.config.tools,
            allowed_user_id=self.config.discord.allowed_user_id,
        )
        self._agent = AgentSession(
            config=self.config.agent,
            session_store=store,
            system_prompt=_resolve_system_prompt(self.config.agent),
            mcp_server=mcp_server,
            allowed_tools=allowed_tools,
            usage_log_path=self._usage_log_path,
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

        # Pre-sleep warning task. Watches OS sleep timer + last user input
        # and posts plain channel.send() reminders so the operator can
        # !stay before the PC drops into Modern Standby. Zero token cost.
        if self.config.keepalive.enabled:
            self._start_keepalive(session)

        if self.config.attach.enabled:
            self._attach = AttachServer(
                self._broker, self.config.attach.host, self.config.attach.port
            )
            await self._attach.start()

        # Sync the slash command tree to the allowed guild. Guild-scoped
        # commands appear immediately (vs. global commands which take up
        # to an hour to propagate).
        guild = discord.Object(id=self.config.discord.allowed_guild_id)
        try:
            synced = await self.tree.sync(guild=guild)
            log.info("synced %d slash command(s) to guild %s", len(synced), guild.id)
        except Exception:
            log.exception("slash sync failed; / commands will not appear")

    def _start_keepalive(self, session: SessionState) -> None:
        thresholds = tuple(m * 60 for m in self.config.keepalive.warn_at_minutes)
        keepalive = KeepAlive(
            session=session,
            notifier=self._post_to_allowed_channel,
            thresholds_seconds=thresholds,
            poll_interval_s=float(self.config.keepalive.poll_interval_seconds),
            warn_on_battery=self.config.keepalive.warn_on_battery,
            stay_command=self.config.discord.stay_command,
        )
        self._keepalive_stop = asyncio.Event()
        self._keepalive_task = asyncio.create_task(keepalive.run(self._keepalive_stop))
        log.info(
            "keepalive armed: thresholds=%s warn_on_battery=%s poll=%ds",
            list(self.config.keepalive.warn_at_minutes),
            self.config.keepalive.warn_on_battery,
            self.config.keepalive.poll_interval_seconds,
        )

    async def _post_to_allowed_channel(self, content: str) -> None:
        """Send `content` to the configured user channel.

        Used by background tasks (keepalive) that don't have a Message in hand.
        Silent no-op if the channel isn't resolvable yet (e.g. fired before
        on_ready fills the guild cache).
        """
        ch = self.get_channel(self.config.discord.allowed_channel_id)
        if ch is None:
            try:
                ch = await self.fetch_channel(self.config.discord.allowed_channel_id)
            except Exception:
                log.debug("notifier could not resolve channel; skipping", exc_info=True)
                return
        if not isinstance(ch, discord.abc.Messageable):
            log.debug("configured channel is not Messageable; skipping notifier send")
            return
        await ch.send(content)

    async def close(self) -> None:
        if self._attach is not None:
            try:
                await self._attach.stop()
            except Exception:
                log.exception("attach server stop raised")
            self._attach = None
        if self._keepalive_stop is not None:
            self._keepalive_stop.set()
        if self._keepalive_task is not None:
            try:
                await self._keepalive_task
            except Exception:
                log.exception("keepalive loop crashed on shutdown")
            self._keepalive_task = None
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

        match = find_by_bang(message.content, self._commands)
        if match is not None:
            cmd, args = match
            await self._run_bang_command(cmd.spec.name, message.channel, args)
            return

        await self._dispatch_to_agent(message)

    # ----------------------------------------------------------------- #
    # Slash command registration                                        #
    # ----------------------------------------------------------------- #

    def _register_slash_handlers(self) -> None:
        """Register a Discord application command for every spec with slash=True.

        Auth is re-checked inside each callback against the configured
        user/channel/guild. Guild-scoping limits *where* the command is
        visible; channel + user gates limit *who* can run it.
        """
        guild = discord.Object(id=self.config.discord.allowed_guild_id)
        for cmd in self._commands:
            if not cmd.spec.slash:
                continue
            self._register_one_slash(cmd, guild)

    def _register_one_slash(self, cmd: ResolvedCommand, guild: discord.Object) -> None:
        name = cmd.spec.name
        # Discord caps command description at 100 chars.
        description = cmd.spec.summary[:100]

        if cmd.spec.takes_args:
            arg_description = "optional arguments (see /help <name>)"[:100]

            @self.tree.command(name=name, description=description, guild=guild)
            @app_commands.describe(args=arg_description)
            async def _slash_with_args(
                interaction: discord.Interaction, args: str = ""
            ) -> None:
                await self._run_slash_command(name, interaction, args)
        else:

            @self.tree.command(name=name, description=description, guild=guild)
            async def _slash_no_args(interaction: discord.Interaction) -> None:
                await self._run_slash_command(name, interaction, "")

    async def _run_slash_command(
        self, name: str, interaction: discord.Interaction, args: str
    ) -> None:
        guild_id = interaction.guild_id
        channel = interaction.channel
        channel_id = channel.id if channel is not None else 0
        if not is_authorized(
            user_id=interaction.user.id,
            channel_id=channel_id,
            guild_id=guild_id,
            config=self.config.discord,
        ):
            log.debug(
                "rejecting unauthorized slash /%s from user=%s channel=%s guild=%s",
                name,
                interaction.user.id,
                channel_id,
                guild_id,
            )
            try:
                await interaction.response.send_message(
                    "not authorized", ephemeral=True
                )
            except Exception:
                log.debug("ephemeral auth-deny send failed", exc_info=True)
            return

        if name == "screenshot":
            # Slow (capture + encode). Defer so Discord doesn't time out.
            try:
                await interaction.response.defer(thinking=True)
            except Exception:
                log.debug("defer failed for /screenshot", exc_info=True)
            assert channel is not None  # auth gate ensures channel is the allowed one
            await self._do_screenshot(channel)
            try:
                await interaction.followup.send("screenshot posted.", ephemeral=True)
            except Exception:
                log.debug("/screenshot followup ack failed", exc_info=True)
            return

        text = await self._invoke_text_command(name, args)
        if text is None:
            text = f"unknown command: {name}"
        try:
            await interaction.response.send_message(text)
        except discord.InteractionResponded:
            await interaction.followup.send(text)
        except Exception:
            log.exception("slash /%s response failed", name)

    # ----------------------------------------------------------------- #
    # Bang command dispatch                                             #
    # ----------------------------------------------------------------- #

    async def _run_bang_command(
        self, name: str, channel: discord.abc.Messageable, args: str
    ) -> None:
        if name == "screenshot":
            # Not actually reachable today (screenshot has no bang surface)
            # but keep the dispatch consistent for future changes.
            await self._do_screenshot(channel)
            return
        text = await self._invoke_text_command(name, args)
        if text is None:
            return
        # Long help / cost output can exceed Discord's 2000-char message
        # cap. Spill to an attachment when over the safety threshold.
        if len(text) > 1900:
            buf = io.BytesIO(text.encode("utf-8"))
            await channel.send(
                content="output too long for one message; full text attached:",
                file=discord.File(buf, filename=f"{name}.txt"),
            )
            return
        await channel.send(text)

    async def _invoke_text_command(self, name: str, args: str) -> str | None:
        """Run a text-producing handler. Returns the text to send, or None
        if the command produces no text response (e.g. screenshot)."""
        if name == "help":
            return self._do_help(args)
        if name == "ping":
            return "pong"
        if name == "stop":
            return await self._do_stop()
        if name == "status":
            return self._do_status()
        if name == "stay":
            return self._do_stay()
        if name == "cost":
            return self._do_cost(args)
        log.warning("text command not implemented: %s", name)
        return None

    # ----------------------------------------------------------------- #
    # Individual command handlers                                       #
    # ----------------------------------------------------------------- #

    def _do_help(self, args: str) -> str:
        args = args.strip()
        if not args:
            return render_overview(self._commands)
        # Allow "!help cost", "!help /cost", "!help !cost" — strip the prefix.
        target = args.lstrip("/!").strip()
        detail = render_detail(target, self._commands)
        if detail is None:
            return f"no such command: `{args}`. Try `!help` for the list."
        return detail

    async def _do_stop(self) -> str:
        log.info("stop requested")
        if self._broker is None or not self._broker.is_busy:
            return "nothing in flight."
        await self._broker.interrupt()
        return "stopped."

    def _do_status(self) -> str:
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
        return f"```\n{text}\n```"

    def _do_stay(self) -> str:
        """Hold the PC awake. Pings the session; no agent dispatch."""
        if self._session is None:
            return ":x: session not initialized; try again in a moment."
        self._session.ping("manual !stay")
        idle_minutes = self.config.session.idle_timeout_minutes
        return (
            f":coffee: staying awake. PowerRequest held for ~{idle_minutes}m of inactivity. "
            f"Send another `{self.config.discord.stay_command}` (or any message) to extend."
        )

    def _do_cost(self, args: str) -> str:
        try:
            period = usage.parse_period(args)
        except ValueError as e:
            return f":x: {e}"
        return usage.summarize(self._usage_log_path, period)

    async def _do_screenshot(self, channel: discord.abc.Messageable) -> None:
        """Capture and post a screenshot directly. Skips the LLM.

        Reuses the existing screenshot tool by setting the channel context
        the way the agent does. We don't surface the SDK content blocks
        (we're not in an agent turn) -- just the side effect of posting
        the image to chat.
        """
        from .tools.screen import _screenshot  # local import: avoid circular at module load

        token = set_channel(channel)
        try:
            result = await _screenshot({"monitor": 0})
        finally:
            reset_channel(token)
        # The tool already posted the image. Surface any error.
        if isinstance(result, dict) and result.get("is_error"):
            text_blocks = [
                blk.get("text", "")
                for blk in result.get("content", [])
                if isinstance(blk, dict) and blk.get("type") == "text"
            ]
            err = " ".join(t for t in text_blocks if t) or "screenshot failed"
            await channel.send(f":x: {err}")

    # ----------------------------------------------------------------- #
    # Agent dispatch (unchanged shape, fed only on the cold path)       #
    # ----------------------------------------------------------------- #

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

        images = await self._extract_image_attachments(message)
        prompt: str | UserTurn
        if images:
            prompt = UserTurn(text=message.content, images=tuple(images))
            log.info("dispatching turn with %d image attachment(s)", len(images))
        else:
            prompt = message.content

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
            ok = self._broker.submit_nowait(prompt, "discord", extra_sinks=[sink])
            if not ok:
                await message.channel.send(
                    ":hourglass: still working on the previous message; use `!stop` to abort"
                )
                reset_channel(token)
                return
            task = asyncio.create_task(_finalize())
            self._finalize_tasks.add(task)
            task.add_done_callback(self._finalize_tasks.discard)

    async def _extract_image_attachments(
        self, message: discord.Message
    ) -> list[ImageBlob]:
        """Pull image attachments off a message. Skips non-images and oversize files.

        5 MB per image is the cap -- comfortably under Discord's 25 MB upload
        limit and the Anthropic API's per-image size limit. Oversize images
        are logged and skipped rather than silently truncated; the user gets
        a "your image was too big" reply if every attachment was rejected.
        """
        max_bytes = 5 * 1024 * 1024
        accepted: list[ImageBlob] = []
        rejected: list[str] = []
        for att in message.attachments:
            ct = (att.content_type or "").lower()
            mime = ct.split(";")[0].strip()
            if not mime.startswith("image/"):
                continue
            if att.size > max_bytes:
                rejected.append(f"{att.filename} ({att.size} bytes)")
                continue
            try:
                data = await att.read()
            except Exception:
                log.exception("download failed for attachment %s", att.filename)
                rejected.append(att.filename)
                continue
            accepted.append(ImageBlob(mime_type=mime, data=data))
        if rejected and not accepted:
            await message.channel.send(
                ":warning: attachment(s) skipped (>5 MB or download failed): "
                + ", ".join(rejected)
            )
        elif rejected:
            log.info("partial attachment rejection: %s", ", ".join(rejected))
        return accepted


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
