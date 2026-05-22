from __future__ import annotations

import logging

import discord

from .auth import is_authorized_message
from .config import Config, Secrets

log = logging.getLogger(__name__)

STOP_COMMAND = "!stop"


class DispatchBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.config: Config = config

    async def on_ready(self) -> None:
        user = self.user
        if user is not None:
            log.info("connected as %s (id=%s)", user, user.id)
        else:
            log.info("connected (no user object yet)")

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

        # !stop is matched verbatim before any other processing. Step 4 will wire
        # this to cancel an in-flight agent turn; for now it just acknowledges.
        if message.content == STOP_COMMAND:
            log.info("!stop received from user=%s", message.author.id)
            await message.channel.send("stopped.")
            return

        if message.content.strip().lower() == "ping":
            await message.channel.send("pong")
            return

        # Step 3 onward will dispatch to the Claude Agent SDK here.
        log.info("authorized message accepted (no agent wired yet): %r", message.content[:120])


def run_bot(config: Config, secrets: Secrets) -> None:
    bot = DispatchBot(config)
    bot.run(secrets.discord_bot_token, log_handler=None)
