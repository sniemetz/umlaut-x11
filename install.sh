#!/bin/bash
# Ümlaut Installation Script

set -e

cd "$(dirname "$(realpath "$0")")"

# Check for Wayland - daemon requires X11
if [ "$XDG_SESSION_TYPE" = "wayland" ] || [ -n "$WAYLAND_DISPLAY" ]; then
    echo ""
    echo "ERROR: Wayland session detected."
    echo ""
    echo "Umlaut requires an X11 session. The daemon uses xdotool for"
    echo "Unicode output, which does not work under Wayland."
    echo ""
    echo "To use Umlaut, log out and select an X11 session at login."
    echo "On Linux Mint: choose 'Cinnamon' (not 'Cinnamon (Wayland)')."
    echo ""
    exit 1
fi


RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: This script must be run as root${NC}"
    echo "Usage: sudo ./install.sh"
    exit 1
fi

echo -e "${GREEN}Ümlaut Installation${NC}"
echo "======================="

# Detect actual user
ACTUAL_USER="${SUDO_USER:-$USER}"
if [ "$ACTUAL_USER" = "root" ]; then
    echo -e "${YELLOW}Warning: Running directly as root (not via sudo)${NC}"
    echo -n "Enter the username that will use Ümlaut: "
    read ACTUAL_USER
fi

USER_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
echo -e "Installing for user: ${GREEN}$ACTUAL_USER${NC}"

# Check dependencies
echo -e "\n${YELLOW}Checking dependencies...${NC}"
DEPS_MISSING=0

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 not found${NC}"
    DEPS_MISSING=1
else
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_OK=$(python3 -c "import sys; print(int(sys.version_info >= (3,8)))")
    if [ "$PY_OK" = "1" ]; then
        echo -e "${GREEN}✓ Python ${PY_VER} found${NC}"
    else
        echo -e "${RED}✗ Python ${PY_VER} found — 3.8 or newer required${NC}"
        DEPS_MISSING=1
    fi
fi

if ! python3 -c "import evdev" 2>/dev/null; then
    echo -e "${RED}✗ python3-evdev not found${NC}"
    echo "  Install with: sudo apt install python3-evdev"
    DEPS_MISSING=1
else
    echo -e "${GREEN}✓ python3-evdev found${NC}"
fi

if ! command -v xdotool &> /dev/null; then
    echo -e "${YELLOW}⚠ xdotool not found (needed for Unicode output)${NC}"
    echo "  Install with: sudo apt install xdotool"
    DEPS_MISSING=1
else
    echo -e "${GREEN}✓ xdotool found${NC}"
fi

# Check AppIndicator for tray applet
APPLET_AVAILABLE=0
if python3 -c "import gi; gi.require_version('AyatanaAppIndicator3', '0.1'); from gi.repository import AyatanaAppIndicator3" 2>/dev/null; then
    echo -e "${GREEN}✓ AyatanaAppIndicator3 found${NC}"
    APPLET_AVAILABLE=1
elif python3 -c "import gi; gi.require_version('AppIndicator3', '0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
    echo -e "${GREEN}✓ AppIndicator3 found${NC}"
    APPLET_AVAILABLE=1
else
    echo -e "${YELLOW}⚠ AppIndicator3 not found (tray applet unavailable)${NC}"
    echo "  Install with: sudo apt install gir1.2-ayatanaappindicator3-0.1"
fi

if [ $DEPS_MISSING -eq 1 ]; then
    echo -e "\n${RED}Missing required dependencies. Install with:${NC}"
    echo "  sudo apt install python3-evdev xdotool"
    exit 1
fi

# Setup input group
echo -e "\n${YELLOW}Setting up input device access...${NC}"

if ! getent group input > /dev/null 2>&1; then
    groupadd input
    echo -e "${GREEN}✓ Created input group${NC}"
fi

NEEDS_RELOGIN=0
if ! groups "$ACTUAL_USER" | grep -q '\binput\b'; then
    usermod -a -G input "$ACTUAL_USER"
    echo -e "${GREEN}✓ Added $ACTUAL_USER to input group${NC}"
    NEEDS_RELOGIN=1
else
    echo -e "${GREEN}✓ $ACTUAL_USER already in input group${NC}"
fi

# udev rules
echo -e "\n${YELLOW}Creating udev rules...${NC}"
cat > /etc/udev/rules.d/99-umlaut.rules << 'EOF'
KERNEL=="event*", SUBSYSTEM=="input", MODE="0660", GROUP="input"
KERNEL=="uinput", GROUP="input", MODE="0660"
EOF
udevadm control --reload-rules
udevadm trigger --subsystem-match=input
echo -e "${GREEN}✓ Created udev rules and triggered device scan${NC}"

# Install service binaries
echo -e "\n${YELLOW}Installing service files → /usr/local/bin/umlaut-scripts/${NC}"
mkdir -p /usr/local/bin /usr/local/bin/umlaut-scripts

install -m 755 service/umlaut_daemon.py /usr/local/bin/umlaut-scripts/umlaut_daemon.py
echo -e "${GREEN}✓ Installed daemon${NC}"

install -m 755 service/umlaut /usr/local/bin/umlaut
echo -e "${GREEN}✓ Installed control script → /usr/local/bin/umlaut${NC}"

if [ -f applet/umlaut_config_manager.py ]; then
    install -m 755 lib/umlaut_paths.py /usr/local/bin/umlaut-scripts/umlaut_paths.py
    install -m 755 applet/umlaut_config_manager.py /usr/local/bin/umlaut-scripts/umlaut_config_manager.py
    echo -e "${GREEN}✓ Installed config manager${NC}"
fi

# Install applet
if [ -f applet/umlaut_applet.py ] && [ $APPLET_AVAILABLE -eq 1 ]; then
    install -m 755 applet/umlaut_applet.py /usr/local/bin/umlaut-scripts/umlaut_applet.py
    echo -e "${GREEN}✓ Installed tray applet${NC}"
fi

# Create user config directory
echo -e "\n${YELLOW}Setting up user config...${NC}"
if [ -d "$USER_HOME/.config/umlaut" ]; then
    echo -e "${GREEN}✓ Found user config directory${NC}"
else
    mkdir -p "$USER_HOME/.config/umlaut"
    echo -e "${GREEN}✓ Created user config directory${NC}"
fi
chown -R "$ACTUAL_USER:$ACTUAL_USER" "$USER_HOME/.config/umlaut"

# Install all configs (never overwrite user's copies)
for config_file in config/*.config.json; do
    [ -f "$config_file" ] || continue
    filename=$(basename "$config_file")
    dest="$USER_HOME/.config/umlaut/$filename"
    if [ ! -f "$dest" ]; then
        install -m 644 "$config_file" "$dest"
        chown "$ACTUAL_USER:$ACTUAL_USER" "$dest"
        echo -e "${GREEN}✓ Installed config: $filename${NC}"
    else
        echo -e "  Kept existing: $filename"
    fi
done

# Seed enabled_sequences in settings.config.json if not already present
SETTINGS_FILE="$USER_HOME/.config/umlaut/settings.config.json"
if ! python3 -c "
import json,os; f='$SETTINGS_FILE'
d=json.load(open(f)) if os.path.exists(f) else {}
exit(0 if 'enabled_sequences' in d else 1)
" 2>/dev/null; then
    python3 -c "
import json, glob, os
settings_path = '$SETTINGS_FILE'
names = sorted(
    os.path.basename(f)[:-len('.config.json')]
    for f in glob.glob('config/*.config.json')
    if os.path.basename(f) != 'settings.config.json'
)
existing = {}
if os.path.exists(settings_path):
    try:
        with open(settings_path) as f:
            existing = json.load(f)
    except Exception:
        pass
existing['enabled_sequences'] = names
with open(settings_path, 'w') as f:
    json.dump(existing, f, indent=2)
"
    echo -e "${GREEN}✓ Seeded enabled_sequences in settings${NC}"
fi

# Remove legacy enabled.sequences if present
if [ -f "$USER_HOME/.config/umlaut/enabled.sequences" ]; then
    rm "$USER_HOME/.config/umlaut/enabled.sequences"
    echo -e "${GREEN}✓ Removed legacy enabled.sequences${NC}"
fi


# Install icons if present
if [ -d icons ] && [ "$(ls -A icons/*.png 2>/dev/null)" ]; then
    echo -e "\n${YELLOW}Installing system icons...${NC}"
    mkdir -p /usr/share/pixmaps/umlaut
    count=0
    for icon in icons/*.png; do
        [ -f "$icon" ] && install -m 644 "$icon" "/usr/share/pixmaps/umlaut/$(basename "$icon")" && count=$((count+1))
    done
    echo -e "${GREEN}✓ Installed $count icons → /usr/share/pixmaps/umlaut/${NC}"
    echo -e "${YELLOW}Note: Add custom icon sets in ~/.config/umlaut/icons/, as:${NC}"
    echo -e "${YELLOW}  name.active.png / name.inactive.png / name.error.png${NC}"
fi

# Install systemd user service
echo -e "\n${YELLOW}Installing user systemd service...${NC}"
USER_SYSTEMD_DIR="$USER_HOME/.config/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR"

install -m 644 service/umlaut.service "$USER_SYSTEMD_DIR/umlaut.service"
chown -R "$ACTUAL_USER:$ACTUAL_USER" "$USER_HOME/.config/systemd"
echo -e "${GREEN}✓ Installed user systemd service${NC}"

# Enable service
echo -e "\n${YELLOW}Enabling service...${NC}"
sudo -u "$ACTUAL_USER" XDG_RUNTIME_DIR="/run/user/$(id -u $ACTUAL_USER)" \
    systemctl --user daemon-reload
sudo -u "$ACTUAL_USER" XDG_RUNTIME_DIR="/run/user/$(id -u $ACTUAL_USER)" \
    systemctl --user enable umlaut
echo -e "${GREEN}✓ Service enabled${NC}"

# Restart daemon if already running
if sudo -u "$ACTUAL_USER" XDG_RUNTIME_DIR="/run/user/$(id -u $ACTUAL_USER)" \
    systemctl --user is-active --quiet umlaut; then
    echo -e "\n${YELLOW}Restarting daemon...${NC}"
    sudo -u "$ACTUAL_USER" XDG_RUNTIME_DIR="/run/user/$(id -u $ACTUAL_USER)" \
        systemctl --user restart umlaut
    echo -e "${GREEN}✓ Daemon restarted${NC}"
fi

# Restart applet if already running
if pgrep -u "$ACTUAL_USER" -f umlaut_applet > /dev/null 2>&1; then
    echo -e "${YELLOW}Restarting applet...${NC}"
    pkill -u "$ACTUAL_USER" -f umlaut_applet || true
    sleep 0.5
    sudo -u "$ACTUAL_USER" DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u $ACTUAL_USER)/bus" \
        /usr/local/bin/umlaut-scripts/umlaut_applet.py &
    echo -e "${GREEN}✓ Applet restarted${NC}"
fi

# Install autostart entry for applet
if [ -f applet/umlaut-applet.desktop ] && [ $APPLET_AVAILABLE -eq 1 ]; then
    AUTOSTART_DIR="$USER_HOME/.config/autostart"
    mkdir -p "$AUTOSTART_DIR"
    install -m 644 applet/umlaut-applet.desktop "$AUTOSTART_DIR/umlaut-applet.desktop"
    chown "$ACTUAL_USER:$ACTUAL_USER" "$AUTOSTART_DIR/umlaut-applet.desktop"
    echo -e "${GREEN}✓ Installed tray applet autostart${NC}"
fi

echo -e "\n${GREEN}Installation complete!${NC}"

if [ "${NEEDS_RELOGIN:-0}" -eq 1 ]; then
    echo -e "\n${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}⚠  IMPORTANT: YOU MUST LOG OUT AND BACK IN NOW  ⚠${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Why?${NC} You were just added to the 'input' group."
    echo -e "${YELLOW}Reason:${NC} Linux loads group membership at login time only."
    echo -e "${YELLOW}Impact:${NC} Ümlaut daemon needs 'input' group to read keyboard devices."
    echo -e ""
    echo -e "${YELLOW}What to do:${NC}"
    echo -e "  1. Save your work"
    echo -e "  2. Log out (not just lock screen!)"
    echo -e "  3. Log back in"
    echo -e "  4. Run: ${GREEN}umlaut start${NC}"
    echo -e ""
    echo -e "${RED}If you run 'umlaut start' now, it will FAIL with permission errors.${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
else
    echo -e "\nStart the daemon: ${GREEN}umlaut start${NC}"
    echo -e "Or manually: ${GREEN}systemctl --user start umlaut${NC}"
    
    if [ $APPLET_AVAILABLE -eq 1 ]; then
        echo -e "\nStart the tray applet: ${GREEN}/usr/local/bin/umlaut-scripts/umlaut_applet.py &${NC}"
        echo -e "(Or it will auto-start on next login)"
    fi
fi

echo -e "\nOther commands (no sudo needed!):"
echo "  umlaut stop              Stop the daemon"
echo "  umlaut restart           Restart the daemon"
echo "  umlaut status            Check status"
echo "  umlaut config-list       List configs"
echo "  umlaut config-enable german symbols     Enable configs"
