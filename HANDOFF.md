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
copy .env.example .env
copy config.toml.example config.toml
```

Edit:

- `.env`            : fill in `ANTHROPIC_API_KEY` and `DISCORD_BOT_TOKEN`.
- `config.toml`     : set `allowed_user_id`, `allowed_channel_id`,
  `allowed_guild_id` to your real Discord IDs (Developer Mode -> right
  click -> Copy ID).

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

## 3. NSSM service install

Download nssm.exe from <https://nssm.cc/> and drop it somewhere stable,
e.g. `C:\Tools\nssm.exe`.

In an **elevated** PowerShell:

```powershell
cd C:\path\to\DiscordClaudeControlBot
.\src\discord_claude_control\service\nssm_install.ps1 -NssmPath C:\Tools\nssm.exe
```

The script prints follow-up instructions. The critical manual step:

```powershell
& 'C:\Tools\nssm.exe' edit discord-claude-control
```

In the dialog that opens, **Log on** tab -> "This account" -> enter
`.\<your-username>` and your Windows password. (LocalSystem can't talk to
your visible desktop, so screenshot and input tools would otherwise
fail.)

Then:

```powershell
& 'C:\Tools\nssm.exe' start discord-claude-control
& 'C:\Tools\nssm.exe' status discord-claude-control
.\src\discord_claude_control\service\nssm_status.ps1
```

Check `logs\stdout.log` and `logs\stderr.log` for any errors. Re-test
the smoke checklist from step 1 with the service running.

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

## 5. Audit log spot-check

After a few tool calls, look at `audit.log` (default in the repo root):

```powershell
Get-Content audit.log -Tail 20
```

Each line is one JSON entry with `ts`, `tool`, `args`, `is_error`, and
`result`. Rotation kicks in at 5 MB.

## Known gaps to address later

- `input_auth_mode = "confirm_destructive"` and `"confirm_all"` are
  config-recognized but not yet implemented; both currently behave like
  `"autonomous"`. There's a warning log at startup.
- Tool calls render in Discord only via the model's text response
  (and screenshots as attachments). The "compact embed per tool call"
  rendering in the original spec is a polish pass we can layer in later.
- Confirmation prompts for `kill_process` / `launch_app` likewise.
- The `truncated, full output saved to X` hard-file-spill behavior on
  PowerShell output is replaced by inline truncation markers. If you
  want the full-file dump back, it's a small follow-up.

None of these block the core workflow.
