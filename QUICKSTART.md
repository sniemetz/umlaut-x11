# Ümlaut — Quick Start

## Installation

```bash
unzip umlaut.zip
cd umlaut
sudo ./install.sh
```

If this is your first install, you'll be prompted to log out and back in
(required once, so Linux activates your new `input` group membership).

## First test

Press **Alt + ;** then **a** → should output **ä**

If that works, you're done.

## Managing configs via GUI

Click the tray icon → **Configure**

- **Sequences tab**: enable/disable config files with checkboxes, edit sequences
- **Settings tab**: change trigger key, timeout, passthrough keys

## Managing configs via CLI

```bash
umlaut config-list                        # Show all configs and status
umlaut config-enable germanicromance      # Enable a config
umlaut config-disable symbols             # Disable a config
umlaut restart                            # Restart daemon
```

## Daemon commands

```bash
umlaut start      # Start daemon
umlaut stop       # Stop daemon
umlaut restart    # Restart daemon
umlaut status     # Check if running
```

## Troubleshooting

**Sequences not working:**
```bash
journalctl --user -u umlaut -n 50    # Check daemon logs
groups | grep input                   # Verify input group (needs re-login if missing)
```

**Tray icon missing:**
```bash
/usr/local/bin/umlaut-scripts/umlaut_applet.py &
```

**Config manager won't open:**
```bash
/usr/local/bin/umlaut-scripts/umlaut_config_manager.py
```

## Uninstall

```bash
sudo ./uninstall.sh
```
