# ClaudeMeter

A tiny system tray / menu bar app that shows how much of your Claude subscription usage you have left, so you know before you hit the limit.

Works on macOS, Windows and Linux.

## What it shows

The icon displays the percent remaining on whichever limit will run out first: the 5 hour session limit or the 7 day weekly limit.

| Color | Meaning |
|-------|---------|
| Orange | More than 25% left |
| Amber | 25% or less left |
| Red | 10% or less left |
| Gray | No data (network error) |

Click the icon to see:

- Session (5h) percent left and time until reset
- Weekly (7d) percent left and time until reset
- Last update time
- Refresh, Open Usage Page, Dark Mode, Set Session Key, Quit

It refreshes automatically every 2 minutes.

**Dark Mode** (toggle in the menu) switches the icon to plain white with the number cut out, to match other monochrome menu bar and tray icons. The setting is saved in `~/.claude-meter/config.json`.

## Requirements

- Python 3.9 or newer
- A Claude Pro or Max subscription

Dependencies (`pystray`, `Pillow`, `keyring`, `curl_cffi`) install automatically on first run into a private virtual environment at `~/.claude-meter/venv`. Your system Python is not modified.

## Quick start

```
git clone https://github.com/JustinOros/claude-meter.git
cd claude-meter
python3 claude_meter.py
```

On Windows use `python` instead of `python3`.

The first launch takes about a minute while dependencies install. You will then be asked for your session key.

## Getting your session key

ClaudeMeter reads your usage the same way the claude.ai Settings > Usage page does, so it needs the `sessionKey` cookie from a browser where you are logged in to claude.ai.

1. Open https://claude.ai and make sure you are logged in.
2. Open Developer Tools:
   - macOS: `Cmd+Option+I`
   - Windows / Linux: `F12` or `Ctrl+Shift+I`
   - Safari: first enable Settings > Advanced > Show features for web developers
3. Find the cookies list:
   - Chrome, Edge, Brave: Application tab > Cookies > https://claude.ai
   - Safari: Storage tab > Cookies
   - Firefox: Storage tab > Cookies > https://claude.ai
4. Copy the value of the `sessionKey` row. It starts with `sk-ant-sid`.
5. Paste it into the ClaudeMeter dialog and click Save.

The key is stored in your system keychain (macOS Keychain, Windows Credential Manager, or Linux Secret Service). If no keychain is available it falls back to `~/.claude_meter_key` with owner only permissions.

**Treat this key like a password.** Anyone with it can access your Claude account. If you log out of claude.ai in that browser, the key stops working and you will need to set a new one from the menu.

## Command line options

| Option | Description |
|--------|-------------|
| (none) | Run the tray app |
| `--install` | Copy to `~/.claude-meter/`, start automatically at login, and launch now |
| `--uninstall` | Stop starting at login |
| `--prompt-key` | Show the session key dialog |
| `--check` | Print your org and raw usage data, or the exact error, then exit |

## Start at login

```
python3 claude_meter.py --install
```

| Platform | Method |
|----------|--------|
| macOS | LaunchAgent at `~/Library/LaunchAgents/com.justinoros.claudemeter.plist` |
| Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` entry using `pythonw` (no console window) |
| Linux | `~/.config/autostart/claudemeter.desktop` |

To update, run `--install` again from the newer copy. To remove, run `--uninstall`, then delete `~/.claude-meter` to remove everything including the virtual environment.

## Troubleshooting

**Icon shows `!`**
claude.ai rejected the request. Click the icon to see the reason. Usually the session key expired: choose Set Session Key and paste a new one. Run `python3 claude_meter.py --check` for full details.

**Icon shows `?`**
No session key is saved. Choose Set Session Key from the menu.

**macOS: does not start at login**
Open System Settings > General > Login Items & Extensions and make sure Python is enabled under Allow in the Background. Check `~/.claude-meter/claude-meter.log` for errors. You can start it manually with:

```
launchctl kickstart -k gui/$(id -u)/com.justinoros.claudemeter
```

**Linux: no icon appears**
Your desktop needs tray support. On GNOME install the "AppIndicator and KStatusNotifierItem Support" extension. You may also need `python3-tk` and an AppIndicator package such as `gir1.2-ayatanaappindicator3-0.1`.

## How it works

ClaudeMeter calls two endpoints on claude.ai using your session cookie:

- `GET /api/organizations` to find your organization ID
- `GET /api/organizations/{org_id}/usage` to read `five_hour` and `seven_day` utilization and reset times

Requests are sent with `curl_cffi` using Chrome impersonation so they are not blocked by Cloudflare.

## Disclaimer

These are unofficial, undocumented endpoints and may change or break at any time. This project is not affiliated with or endorsed by Anthropic. It tracks Pro and Max plan usage limits only, not prepaid API Console credits.
