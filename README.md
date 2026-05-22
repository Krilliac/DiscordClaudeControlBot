# duet-dispatch

Discord-controlled personal PC agent. Runs as a Windows service on your own
machine; the Discord channel is the only client. A message from your user in
the configured channel becomes a turn in an ongoing conversation with Claude
via the Claude Agent SDK, with tools that operate on your actual desktop
(PowerShell, files, screenshots, mouse/keyboard, processes).

Single-user, single-channel, single-guild. Not multi-tenant.

## Status

Built incrementally. Each step ends with a working, testable artifact.

- [x] Step 1: skeleton + config + auth gating (ping/pong, no agent yet)
- [ ] Step 2: power management (`PowerCreateRequest`)
- [ ] Step 3: Claude Agent SDK loop with streaming responses
- [ ] Step 4: `!stop` hard kill switch wired to cancel the agent
- [ ] Step 5: tools (PowerShell, files, screenshot, input, processes)
- [ ] Step 6: idle/active session state machine
- [ ] Step 7: NSSM service install
- [ ] Step 8: Modern Standby end-to-end validation

## Prerequisites

- Windows 10/11 with Modern Standby (S0 Low Power Idle).
  Verify: `powercfg /a` should list `S0 Low Power Idle`.
- Python 3.12+.
- A Discord application + bot in your own private server. Enable the
  **Message Content Intent** in the Discord Developer Portal.
- Your Discord user, channel, and guild IDs (Developer Mode → right-click → Copy ID).
- An Anthropic API key.
- (Later) [NSSM](https://nssm.cc/) for service install.

## First-time setup

```powershell
git clone <this-repo>
cd duet-dispatch
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env         # fill in ANTHROPIC_API_KEY and DISCORD_BOT_TOKEN
copy config.toml.example config.toml  # fill in user/channel/guild IDs
```

## Run locally

```powershell
python -m duet_dispatch
```

At step 1 the bot only connects, gates on your user/channel/guild, and
replies `pong` to `ping`. Everything else from your account is logged and
ignored. Messages from other accounts or other channels are silently
dropped.

## Tests

```powershell
pytest
```

Unit tests cover the auth allow-list and config parsing. The Discord
client and (later) the agent loop are exercised by running the service
against a real bot token.

## Design notes

### Why NSSM over a native Win32 service?

NSSM is a one-line install (`nssm install duet-dispatch ...`), runs any
Python script as a service, and lets us run under the logged-in user account,
which is required for screenshot/input tools to interact with the visible
desktop session. A native `pywin32` service would buy nothing here and adds
more code to maintain. We'll add the NSSM scripts in step 7.

### Why Modern Standby instead of WoL?

Wake-on-LAN requires the PC to be in S3/S4/S5 and needs the network adapter
to support magic packets while powered down. Modern Standby (S0ix) keeps the
network stack alive at very low power, so the Discord WebSocket stays
connected and an inbound message wakes the CPU naturally. Less moving parts,
no router config.

### Auth model

`auth.is_authorized` checks all three IDs (user, channel, guild) before any
message is forwarded to the agent. DMs are rejected (`guild_id is None`).
`!stop` is matched verbatim before the message reaches the agent so a stuck
agent can always be interrupted.
