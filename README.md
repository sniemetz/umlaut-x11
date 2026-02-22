# Ümlaut

**System-wide keyboard compose sequences for Linux**

Type special characters, insert text snippets, and create custom shortcuts
without switching keyboard layouts. Works in every application.

## How it works

1. Hold your **trigger key** (default: either Alt key)
2. Press a **compose key** (e.g. `;`)
3. Release both
4. Press a **target key** (e.g. `a`) → outputs `ä`

## Features

- Compose sequences for any Unicode character
- Text expansion (e.g. trigger + `F` + `e` → `user@example.com`)
- Multiple config files — enable only what you need
- System tray applet with GUI config manager
- Runs as a user service, no root needed after install

## Requirements

- Linux with X11 (not Wayland)
- Python 3.8+
- `python3-evdev`, `xdotool`
- `gir1.2-ayatanaappindicator3-0.1` (for tray applet)

## Installation

```bash
sudo apt install python3-evdev xdotool gir1.2-ayatanaappindicator3-0.1
sudo ./install.sh
```

Log out and back in after first install (activates `input` group).

## Commands

```bash
umlaut start              # Start daemon
umlaut stop               # Stop daemon
umlaut restart            # Restart daemon
umlaut status             # Check status
umlaut config-list        # List configs and enabled status
umlaut config-enable <name> [name...]   # Enable one or more configs
umlaut config-disable <name> [name...]  # Disable one or more configs
```

## Bundled configs

| File | Contents |
|---|---|
| `germanicromance` | German umlauts, French accents, Spanish characters, ligatures |
| `slavic` | Czech, Polish, Romanian, Croatian, and other Slavic characters |
| `symbols` | Currency, arrows, math, typographic symbols |
| `shortcuts` | Text snippets and common expansions |

All configs are enabled by default. Disable any you don't need.

## Config file format

### `settings.config.json`

| Field | Type | Default | Description |
|---|---|---|---|
| `version` | int | `1` | Schema version — must be `1` |
| `trigger_key` | str or list | `["KEY_LEFTALT","KEY_RIGHTALT"]` | Trigger key(s) |
| `passthrough_keys` | list | `[]` | Keys passed through during compose (e.g. `KEY_TAB` for Alt+Tab) |
| `enabled_sequences` | list | `[]` | Config stems to load (e.g. `["germanicromance"]`) |
| `icon_set` | str | `"default"` | Tray icon theme name |
| `settings.timeout_ms` | int | `1000` | Time to complete a sequence (ms). Range: 100–10000 |
| `settings.log_level` | str | `"INFO"` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

### Sequence config files

| Field | Type | Description |
|---|---|---|
| `version` | int | Must be `1` |
| `name` | str | Display name shown in the GUI |
| `description` | str | Subtitle shown in the GUI |
| `sequences` | dict | Compose key → target key → output string |

#### Key notation

| Notation | Example | Meaning |
|---|---|---|
| Single printable char | `";"`, `"'"`, `","` | That key unshifted |
| Uppercase letter | `"A"` | Shift + that letter |
| Shifted punctuation | `"!"`, `"@"` | Shift + base key |
| Evdev name | `"KEY_ENTER"` | Non-printable key |
| Shifted evdev | `"SHIFT+KEY_SEMICOLON"` | Shift + that key |
| Modifier combo (output only) | `"CTRL+a"` | Key combination as output |

#### Aliases

A compose key can reference another compose key's targets:

```json
"sequences": {
  ";":       { "a": "ä", "o": "ö", "u": "ü" },
  "'":       { "e": "é", "a": "á", "i": "í" },
  "SHIFT+;": ";"
}
```

#### Example sequence config

```json
{
  "version": 1,
  "name": "My Shortcuts",
  "description": "Custom text snippets",
  "sequences": {
    "KEY_F": {
      "e": "user@example.com",
      "p": "+1 555-0100",
      "a": "123 Main St, Springfield"
    }
  }
}
```

## Custom configs

Create a new `.config.json` file in `~/.config/umlaut/`, then enable it:

```bash
umlaut config-enable myconfig
umlaut restart
```

Or use the GUI: tray icon → Configure → New Config.

## Custom tray icons

Place icon files in `~/.config/umlaut/icons/`:
- `<name>.active.png` — daemon running
- `<name>.inactive.png` — daemon stopped
- `<name>.error.png` — daemon error

Then set `icon_set` in settings to `<name>`.

## Troubleshooting

**Daemon won't start:**
```bash
journalctl --user -u umlaut -n 50
groups | grep input    # must be present; re-login if missing
```

**Sequences not working:**
```bash
journalctl --user -u umlaut | grep "Found keyboard"   # check devices grabbed
umlaut restart
```

**Tray icon missing:**
```bash
/usr/local/bin/umlaut-scripts/umlaut_applet.py &
# Check AppIndicator is installed:
dpkg -l | grep appindicator
```

## License

MIT — see LICENSE file.
