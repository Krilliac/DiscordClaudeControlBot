# discord-claude-control

Discord-controlled personal PC agent. Runs as a Windows service on your own
machine; the Discord channel is the only client. A message from your user in
the configured channel becomes a turn in an ongoing conversation with Claude
via the Claude Agent SDK, with tools that operate on your actual desktop
(PowerShell, files, screenshots, mouse/keyboard, processes).

Single-user, single-channel, single-guild. Not multi-tenant.

## NOTICE -- intended use

This project is a **personal remote-control** for the operator's own PC. It
is not a service, not a Claude proxy, not a way to share Claude access, not
a multi-tenant bot. It is functionally equivalent to SSH-ing into your own
machine and typing into your own terminal: same operator, same hardware,
same Claude session, different keyboard.

The code enforces this at the auth layer (`src/.../auth.py`):

- Messages are accepted only from one hardcoded Discord user ID, in one
  hardcoded channel, in one hardcoded guild.
- DMs are rejected.
- The check runs before any LLM dispatch, on every message, with no
  override or admin mode.

If you use **API mode** (`ANTHROPIC_API_KEY` set), you are billed per token
through your own Anthropic API account; standard API terms apply.

If you use **subscription mode** (`claude /login`, no API key), the bot's
spawned Claude Code talks to Anthropic on your Pro/Max plan. Anthropic's
Pro/Max plans are intended for the subscriber's personal use; this project
is designed to stay within that intent: only YOU (the subscriber) can
trigger turns, your messages drive every interaction, and there is no
unattended automation. If your use case might fall outside personal use
(high-volume background jobs, sharing the channel with other people,
automating prompts without your direct input, etc.), use API mode instead
or check Anthropic's current Usage Policy and Pro/Max terms before running.

**Do not** modify the auth check to allow additional users, additional
channels, or DM access. The single-user property is what keeps this on the
right side of "remote-controlling my own Claude" vs. "running a Claude
service." If you need multi-user, build something with the Anthropic API
under appropriate terms, not this.

Anthropic's Usage Policy: <https://www.anthropic.com/legal/usage-policy>

## Status

Built incrementally. Each step ends with a working, testable artifact.

- [x] Step 1: skeleton + config + auth gating (ping/pong, no agent yet)
- [x] Step 2: power management (`PowerCreateRequest`)
- [x] Step 3: Claude Agent SDK loop with streaming responses
- [x] Step 4: `!stop` hard kill switch wired to cancel the agent
- [x] Step 5: tools (PowerShell, files, screenshot, input, processes)
- [x] Step 6: idle/active session state machine
- [x] Step 7: Unattended install scripts (Task Scheduler primary, NSSM service alternative)
- [x] Step 9 (post-redesign): broker + local attach socket + subscription-auth mode
- [ ] Step 8: Modern Standby end-to-end validation (user-side, after install)

## Auth modes

Pick one (the bot doesn't care which):

1. **API mode** -- put `ANTHROPIC_API_KEY=sk-ant-...` in `.env`. Pay per token.
2. **Subscription mode** -- leave `ANTHROPIC_API_KEY` unset. Run `claude /login`
   once on the PC (interactive browser flow). The bot's spawned Claude Code
   uses your Pro/Max plan, no per-token API charges.

The bot logs at startup which mode it's using.

## Local attach (PC-side direct interaction)

Enable in `config.toml`:

```toml
[attach]
enabled = true
host = "127.0.0.1"   # loopback only -- do not expose
port = 9876
```

Then on the PC, while the service is running:

```powershell
python -m discord_claude_control.attach
```

This connects to the same Claude conversation Discord is driving. Anything
you type in the attach terminal flows in as a user turn. Any responses
(from your turn OR a Discord turn) stream back to all connected attach
clients AND to Discord. Close the attach CLI to detach -- conversation
keeps running.

## Prerequisites

- Windows 10/11 with Modern Standby (S0 Low Power Idle).
  Verify: `powercfg /a` should list `S0 Low Power Idle`.
- Python 3.12+.
- A Discord application + bot in your own private server. Enable the
  **Message Content Intent** in the Discord Developer Portal.
- Your Discord user, channel, and guild IDs (Developer Mode -> right-click -> Copy ID).
- An Anthropic API key.
- For unattended deploy: nothing extra (Task Scheduler ships with Windows).
  Optional: [NSSM](https://nssm.cc/) only if you need a headless service
  with no desktop-interactive tools (see Deployment below).

## First-time setup

```powershell
git clone <this-repo>
cd DiscordClaudeControlBot
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m discord_claude_control.setup    # interactive wizard
```

The wizard walks you through every required value, validates them, and
writes `.env` (secrets) + `config.toml` (the rest). Both are gitignored;
the repo only ships the `.example` files.

If you'd rather do it by hand:

```powershell
copy .env.example .env                 # fill in DISCORD_BOT_TOKEN (and optionally ANTHROPIC_API_KEY)
copy config.toml.example config.toml   # fill in user/channel/guild IDs
```

## Run locally

```powershell
python -m discord_claude_control
```

At step 1 the bot only connects, gates on your user/channel/guild, and
replies `pong` to `ping`. Everything else from your account is logged and
ignored. Messages from other accounts or other channels are silently
dropped.

### Manual power-request check (step 2)

```powershell
python -m discord_claude_control.power_cli --hold 30
```

While the script is sleeping, in another PowerShell run `powercfg /requests`.
You should see `discord-claude-control: active session` under SYSTEM. After
the script exits the entry disappears.

## Tests

```powershell
pytest
```

Unit tests cover the auth allow-list, config parsing, and the
platform-independent state machine inside `power.py`. The real `ctypes`
calls into `kernel32` are integration-tested by running `power_cli` on
the actual PC; on non-Windows the power module degrades to a logging
stub so unit tests can still run.

## Design notes

### Deployment: Task Scheduler primary, NSSM secondary

The bot needs access to your visible desktop -- BitBlt for screenshots,
SendInput for mouse/keyboard, GDI for window queries. None of those
work in **Session 0**, the non-interactive session Windows services
run in (regardless of which account the service logs on as). Session 0
isolation has been the rule since Vista; it's not a configuration we
can flip off.

So the supported deploy is **Task Scheduler at user logon**:

```powershell
.\src\discord_claude_control\service\task_install.ps1
```

The bot runs as a normal child process of your interactive session.
Screenshot, click, type_text, the whole tool set works. The trade-off:
the bot only runs while a user is logged in. After a reboot, sign in
once and it starts a few seconds later; for fully unattended boot,
configure Windows auto-logon (`netplwiz`).

NSSM is shipped as a fallback for the rare case where you need a
service that runs before any user logon AND you don't need the
desktop-interactive tools:

```powershell
.\src\discord_claude_control\service\nssm_install.ps1
```

The NSSM script self-elevates and prompts for your Windows password to
configure log-on as your user account (so the bot can read `%USERPROFILE%`
and HKCU). But `BitBlt` still fails in Session 0 -- disable
`screenshot`, `click`, `move_mouse`, `type_text`, `press_key`, and
`launch_app` in `config.toml`'s `[tools].enabled` list if you go this
route.

### Why NSSM over a native Win32 service?

When a service IS the right shape (headless only), NSSM is a one-line
install, runs any Python script as a service, and supports user-account
log-on. A native `pywin32` service would buy nothing here and adds more
code to maintain.

### Why Modern Standby instead of WoL?

Wake-on-LAN requires the PC to be in S3/S4/S5 and needs the network adapter
to support magic packets while powered down. Modern Standby (S0ix) keeps the
network stack alive at very low power, so the Discord WebSocket stays
connected and an inbound message wakes the CPU naturally. Fewer moving
parts, no router config.

### Auth model

`auth.is_authorized` checks all three IDs (user, channel, guild) before any
message is forwarded to the agent. DMs are rejected (`guild_id is None`).
`!stop` is matched verbatim before the message reaches the agent so a stuck
agent can always be interrupted.

### Power model

`power.PowerRequest` wraps `PowerCreateRequest` / `PowerSetRequest` /
`PowerClearRequest` from `kernel32.dll`. The request is named so it shows
up cleanly in `powercfg /requests`. The class is idempotent on both
acquire and release, and supports use as a context manager. The Windows
call layer is split behind a `_Backend` protocol so the state machine
can be unit-tested on any OS with a stub backend.
