# PC-side handoff checklist

This file lists every action that requires being at the actual Windows
PC, because they can't be done from a remote dev container:

- Real `kernel32.dll` calls (`powercfg /requests` verification)
- A real Anthropic API key + Discord bot token in the live process
- Real `powershell.exe`, mouse/keyboard simulation, screenshots of the
  desktop session
- NSSM service registration (needs Administrator + the Windows password)
- Modern Standby sleep/wake validation

Work through this list once when you sit back down. Each step is short
and stand-alone; you can stop after any subset.

## 0. Sanity check

```powershell
cd C:\path\to\DiscordClaudeControlBot
git pull origin claude/discord-pc-agent-IIXaP
powercfg /a
```

Confirm `S0 Low Power Idle` appears in the output. If it doesn't, Modern
Standby is not available and step 8 won't work.

## 1. Install + first-run smoke test

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m discord_claude_control.setup
```

The wizard prompts for the Discord bot token, your three Discord IDs
(user/channel/guild), optional Anthropic API key, model, idle timeout,
and whether to enable the attach socket. It validates each value
(token length, snowflake format, positive integers), then writes
`.env` + `config.toml`.

If you'd rather edit by hand: `copy .env.example .env` and
`copy config.toml.example config.toml`, then fill them in. The example
files are extensively commented.

## 1b. Choose auth mode (one of these)

**Subscription mode (no API cost):** run once,
```powershell
claude /login
```
Pick your Claude Pro/Max account in the browser. From then on the bot's
Claude Code child process uses that auth. Leave `ANTHROPIC_API_KEY` empty.

**API mode (pay-per-token):** put `ANTHROPIC_API_KEY=sk-ant-...` into
`.env`. No `claude /login` needed.

The startup logs say which mode is active:
```
authenticating via ANTHROPIC_API_KEY (pay-per-token)
no ANTHROPIC_API_KEY set; expecting Claude Code to be authenticated via `claude /login` (subscription mode)
```

In the Discord Developer Portal for your bot, **enable the Message
Content Intent**. The bot will silently never see messages without it.

Run the bot foreground:

```powershell
python -m discord_claude_control
```

From your phone or another Discord client, in the configured channel:

| Message                          | Expected behavior                                                |
|----------------------------------|------------------------------------------------------------------|
| `ping`                           | bot replies `pong` (no tokens spent)                             |
| `say hi`                         | first agent turn streams in; reply appears as text               |
| `take a screenshot of my screen` | screenshot posts as an attachment to the channel                 |
| `list the files in C:\`          | `list_dir` runs, output truncated if needed                      |
| `run "Get-Service" via powershell` | `run_powershell` runs, exit code + stdout/stderr posted        |
| `!stop` while a long turn is mid-stream | reply edits stop; bot posts "stopped."                    |
| anything from another account or another channel | total silence                                  |

Now send a message from a different account in the same guild, and a
message from your account in a different channel. Both should be silently
ignored. (The bot logs them at DEBUG; nothing appears in Discord.)

## 2. Power request verification

In a second elevated PowerShell:

```powershell
cd C:\path\to\DiscordClaudeControlBot
.venv\Scripts\Activate.ps1
python -m discord_claude_control.power_cli --hold 30
```

In a third PowerShell, while the script is in its sleep:

```powershell
powercfg /requests
```

You should see `[SYSTEM] discord-claude-control: active session` under
the SYSTEM heading. Once the script exits the line disappears.

If `using backend: stub` appears in the CLI output, the Windows backend
failed to load -- check the python version, kernel32 availability, or
report back.

## 3. Install at user logon (Task Scheduler) -- RECOMMENDED

This is the supported path for desktop-aware automation. The bot runs in
your interactive session (Session 1+), where `BitBlt` and the rest of
the screenshot/input tools actually work.

```powershell
cd C:\path\to\DiscordClaudeControlBot
.\src\discord_claude_control\service\task_install.ps1
```

No elevation. No password prompt. The script:

- Registers `\discord-claude-control\bot` triggered at your logon.
- Wraps python in `cmd.exe` so stdout/stderr go to `logs\stdout.log` and
  `logs\stderr.log`.
- Starts it immediately (pass `-NoStart` to defer until next logon).

Verify:

```powershell
.\src\discord_claude_control\service\task_status.ps1
Get-Content logs\stderr.log -Tail 10
```

You should see `connected as <bot>#... (id=...)` near the bottom. Then
re-test the smoke checklist from step 1.

Restart (after a code change or config edit):

```powershell
.\src\discord_claude_control\service\task_restart.ps1
```

Do NOT do `Stop-ScheduledTask` + `Start-ScheduledTask` by hand. If a
prior python.exe has orphaned from its cmd.exe wrapper (cmd died,
python kept going as a child of services.exe),
`Stop-ScheduledTask` leaves it alive -- the new bot you then start
loses the Discord gateway-slot race against the orphan and exits
code 1, while the orphan keeps serving stale code as if nothing
happened. `task_restart.ps1` finds every python.exe whose
CommandLine references `discord_claude_control`, kills them, waits
for Discord to release the gateway slot, then starts the task and
polls `logs\heartbeat` to confirm the new bot is actually live.
`task_install.ps1` and `task_uninstall.ps1` apply the same kill
sweep before touching the task registration.

Uninstall:

```powershell
.\src\discord_claude_control\service\task_uninstall.ps1
```

### What "survives reboots" means here

The task fires at user logon. After a reboot:

- If you sign in normally, the bot starts a few seconds later. Standard
  case.
- If you want the PC to come up bot-running with no human interaction,
  enable Windows Auto-Logon (`netplwiz` -> uncheck "Users must enter a
  user name and password"). The user session is recreated automatically
  at boot, and the logon trigger fires inside it.

True boot-time start before any user logs on requires a Windows
**service** (step 3 alt.), which can NOT run the desktop-interactive
tools.

## 3 alt. NSSM service (headless tools only)

Use this **only** if you genuinely need a service that survives reboots
without any user logon AND you are willing to disable
`screenshot`/`click`/`move_mouse`/`type_text`/`press_key`/`launch_app`
in `config.toml`. Services run in Session 0, where GDI/USER calls
against the visible desktop fail.

Install NSSM (winget is simplest):

```powershell
winget install --id NSSM.NSSM --source winget
```

Or download from <https://nssm.cc/> and drop `nssm.exe` somewhere stable
(e.g. `C:\Tools\nssm.exe`).

Then run the install script -- it self-elevates and prompts for your
Windows password (used to set the service log-on account):

```powershell
cd C:\path\to\DiscordClaudeControlBot
.\src\discord_claude_control\service\nssm_install.ps1
```

Verify:

```powershell
.\src\discord_claude_control\service\nssm_status.ps1
Get-Content logs\stderr.log -Tail 10
```

Uninstall:

```powershell
.\src\discord_claude_control\service\nssm_uninstall.ps1
```

## 4. Modern Standby end-to-end

This is the final validation that the whole thing actually does what we
designed it for:

1. With the service running and confirmed responsive (`ping` -> `pong`),
   close your laptop lid or trigger Sleep from the Start menu.
2. Wait ~1 minute so the PC settles into S0ix.
3. From your phone, send a non-trivial message in the channel
   (e.g. `say hello`).
4. The PC should wake (no fan, screen stays off) and reply within a few
   seconds. Open the lid -- nothing else should look amiss.
5. Stop using the bot and wait `idle_timeout_minutes` (default 10).
   `powercfg /requests` should now show no `discord-claude-control`
   entry, and the PC should be free to return to S0ix on its own.

If the PC fails to wake on inbound message, the Discord WebSocket is
either dropping during Modern Standby or being throttled by the network
adapter's power-savings settings. Try:

- Disable "Allow the computer to turn off this device to save power"
  on the network adapter (`devmgmt.msc` -> network adapter -> Power
  Management).
- Re-confirm `powercfg /a` lists S0 Low Power Idle.

## 4b. Local attach (optional)

If you set `attach.enabled = true` in `config.toml`, with the service
running:

```powershell
python -m discord_claude_control.attach
```

You're now another window onto the same conversation Discord is using.
Type a message + Enter to send. `!stop` interrupts. Ctrl-C detaches
without killing anything.

Test it: in Discord, send `say hi`. The reply should stream into both
Discord AND your attach terminal. Then from the attach terminal type
`now repeat that backwards`. Both Discord and the attach terminal should
see your input echo (prefixed `>>> [attach]`) and the response.

If `python -m discord_claude_control.attach` fails with `connect failed:
[WinError 10061]`, double-check `attach.enabled = true` in your
`config.toml` and that you restarted the service after editing.

## 5. Audit log spot-check

After a few tool calls, look at `audit.log` (default in the repo root):

```powershell
Get-Content audit.log -Tail 20
```

Each line is one JSON entry with `ts`, `tool`, `args`, `is_error`, and
`result`. Rotation kicks in at 5 MB.

## 5b. Usage log spot-check

After a few agent turns, look at `usage.log`:

```powershell
Get-Content usage.log -Tail 5
```

One JSON line per Claude turn with token counts (input / output /
cache create / cache read), `total_cost_usd`, duration, and session
id. Read by the `!cost` / `/cost` command.

## 6. Watchdog install (recommended)

The watchdog is a second, independent process that reads
`logs/heartbeat` and posts an alert + restarts the bot if the
heartbeat goes stale. It uses a **separate Discord webhook URL**
(not the bot's gateway token), so a bot-token reset does not
silence the alerts.

Set up the webhook:

1. In Discord, pick your alerts channel.
2. Channel settings -> Integrations -> Webhooks -> New Webhook.
3. Copy the webhook URL.
4. Add to `.env`:
   ```
   ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/.../...
   ```

Install:

```powershell
.\src\discord_claude_control\service\task_install_watchdog.ps1
```

The script registers the watchdog at user logon (separate Task
Scheduler entry from the bot) and starts it. Logs land in
`logs\watchdog-stdout.log` and `logs\watchdog-stderr.log`. Defaults:
180s stale threshold, 60s poll interval, 600s alert cooldown.

Tune via parameters, e.g.
`task_install_watchdog.ps1 -StaleSeconds 120 -CheckInterval 30`.

Smoke test (manually wedge the bot to verify alerting):

```powershell
# 1. Confirm watchdog is up.
Get-Content logs\watchdog-stdout.log -Tail 5

# 2. Stop the bot (NOT the watchdog).
.\src\discord_claude_control\service\task_uninstall.ps1
# Wait ~3 minutes (the stale threshold).

# 3. Within ~1-3 minutes you should see an alert in your alerts channel.

# 4. Reinstall the bot:
.\src\discord_claude_control\service\task_install.ps1
# The watchdog should post a "recovered" message on the next check.
```

## Command reference

Anything not in this table becomes an LLM turn.

| Message form     | Slash form    | Effect                                              |
|------------------|---------------|-----------------------------------------------------|
| `ping`           | -             | bot replies `pong` (no tokens)                      |
| `!stop`          | `/stop`       | interrupt the current agent turn                    |
| `!status`        | `/status`     | uptime, idle/active, attach clients                 |
| `!help`          | `/help`       | list every command                                  |
| `!help <name>`   | `/help <name>`| detail for one command                              |
| `!cost`          | `/cost`       | today's token + USD usage                           |
| `!cost week`     | `/cost week`  | last 7 days                                         |
| `!cost month`    | `/cost month` | last 30 days                                        |
| `!cost all`      | `/cost all`   | since usage.log started                             |
| -                | `/screenshot` | grab a screenshot directly (no LLM, no tokens)      |

Attach an image file to any Discord message and it gets sent to
Claude as a vision input alongside your text.

## Confirmations

Set `input_auth_mode` in `config.toml`'s `[tools]` section:

- `autonomous` (default): no prompts.
- `confirm_destructive`: bot asks for ✅/❌ on `kill_process` and
  `launch_app`. Tap ✅ within 30s to approve.
- `confirm_all`: same prompt for every input/process tool
  (`kill_process`, `launch_app`, `move_mouse`, `click`,
  `type_text`, `press_key`).

If the confirmation can't reach you (e.g. an attach-only turn with
no live channel), the tool returns an error to the agent rather than
running. Default-deny.

## Known gaps to address later

- Large outputs (PowerShell stdout/stderr > 1500 bytes, oversized
  file reads, large `list_dir` results) now post the full bytes as a
  Discord attachment and return a truncated head to the model. If
  you want a different threshold, set `tools.output_truncate_at` in
  `config.toml`.
- Tool calls render in Discord only via the model's text response
  (and screenshots / large outputs as attachments). The "compact
  embed per tool call" rendering in the original spec is a polish
  pass we can layer in later.
- The attach socket is loopback-only with no authentication. Anyone
  with a local user session on the PC could connect. That's the same
  trust boundary as the desktop itself -- fine for single-user, would
  need an auth handshake for multi-user / shared machines.

None of these block the core workflow.
