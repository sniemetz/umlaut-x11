# Ümlaut — Project Structure

## Repository Layout

```
umlaut/
├── install.sh                  # Installer (requires sudo)
├── uninstall.sh                # Uninstaller
├── lib/
│   └── umlaut_paths.py         # Shared path constants, schemas, config I/O helpers
├── README.md                   # Full documentation
├── QUICKSTART.md               # 5-minute setup guide
├── ROADMAP.md                  # Status and planned features
├── PROJECT_STRUCTURE.md        # This file
│
├── config/                     # Bundled sequence configs (installed to ~/.config/umlaut/)
│   ├── settings.config.json    # Default settings (trigger key, timeout, etc.)
│   ├── germanicromance.config.json
│   ├── slavic.config.json
│   ├── symbols.config.json
│   └── shortcuts.config.json
│
├── icons/                      # Bundled system tray icon set
│   └── *.png                   # Installed to /usr/share/pixmaps/umlaut/
│
├── applet/                     # GUI components
│   ├── umlaut_applet.py        # System tray applet
│   ├── umlaut_config_manager.py# Config manager GUI
│   ├── umlaut_paths.py         # Dev copy (canonical is repo root umlaut_paths.py)
│   └── umlaut-applet.desktop   # Autostart entry
│
└── service/                    # Core daemon and CLI
    ├── umlaut_daemon.py        # Keyboard remapping daemon
    ├── umlaut                  # CLI control script
    ├── umlaut.service          # Systemd user service
    └── test_daemon.py          # Unit tests (40 tests)
```

## Installation Paths

### System files (require sudo)
| Source | Installed to |
|---|---|
| `lib/umlaut_paths.py` | `/usr/local/bin/umlaut-scripts/umlaut_paths.py` |
| `service/umlaut_daemon.py` | `/usr/local/bin/umlaut-scripts/umlaut_daemon.py` |
| `service/umlaut` | `/usr/local/bin/umlaut` |
| `applet/umlaut_config_manager.py` | `/usr/local/bin/umlaut-scripts/umlaut_config_manager.py` |
| `applet/umlaut_applet.py` | `/usr/local/bin/umlaut-scripts/umlaut_applet.py` |
| `icons/*.png` | `/usr/share/pixmaps/umlaut/` |
| udev rules | `/etc/udev/rules.d/99-umlaut.rules` |

### User files (per-user, no sudo)
| Path | Description |
|---|---|
| `~/.config/umlaut/settings.config.json` | Settings: trigger key, timeout, enabled sequences |
| `~/.config/umlaut/*.config.json` | Sequence config files |
| `~/.config/umlaut/icons/` | Custom icon sets (optional) |
| `~/.config/systemd/user/umlaut.service` | User systemd service |
| `~/.config/autostart/umlaut-applet.desktop` | Applet autostart entry |

## Config System

### Load order (daemon startup)
1. `~/.config/umlaut/settings.config.json` — trigger key, passthrough keys, timeout, `enabled_sequences` list
2. Each file listed in `enabled_sequences` is loaded from `~/.config/umlaut/`

### settings.config.json
Controls all daemon behaviour and tracks which sequence configs are active:
```json
{
  "version": 1,
  "trigger_key": ["KEY_LEFTALT", "KEY_RIGHTALT"],
  "passthrough_keys": [],
  "enabled_sequences": ["germanicromance", "symbols"],
  "icon_set": "default",
  "settings": {
    "timeout_ms": 1000,
    "log_level": "INFO"
  }
}
```

### Sequence config files
```json
{
  "version": 1,
  "name": "Germanic & Romance",
  "description": "Umlauts, accents, ligatures",
  "sequences": {
    ";": { "a": "ä", "o": "ö", "u": "ü", "s": "ß" },
    "'": { "e": "é", "a": "á", "i": "í" }
  }
}
```

## Shared Infrastructure

`lib/umlaut_paths.py` is imported by all three components (daemon, config manager, CLI):
- Path constants (`USER_CONFIG_DIR`, `USER_SETTINGS`, etc.)
- `SETTINGS_SCHEMA` / `SEQUENCE_SCHEMA` — canonical key/default definitions
- `load_settings()` / `save_settings()` — schema-validated I/O
- `load_sequence_config()` — loads, cleans, and validates sequence files

## Running Tests

```bash
cd service/
python3 test_daemon.py
# Ran 40 tests in ~0.02s — OK
```
