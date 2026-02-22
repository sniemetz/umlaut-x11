#!/usr/bin/env bash
# Umlaut uninstaller

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Must NOT be run as root (user systemd commands require the actual user)
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Do not run as root. Run as your normal user (sudo will be invoked where needed).${NC}"
    exit 1
fi

echo -e "${YELLOW}Stopping umlaut...${NC}"
pkill -f umlaut_applet.py 2>/dev/null || true
pkill -f umlaut_daemon.py 2>/dev/null || true
pkill -f umlaut_config_manager.py 2>/dev/null || true

systemctl --user stop umlaut.service 2>/dev/null || true
systemctl --user disable umlaut.service 2>/dev/null || true
echo -e "${GREEN}✓ Stopped and disabled service${NC}"

echo -e "\n${YELLOW}Removing system files...${NC}"
sudo rm -rf /usr/local/bin/umlaut-scripts
sudo rm -f  /usr/local/bin/umlaut
sudo rm -rf /usr/share/pixmaps/umlaut
sudo rm -f  /etc/udev/rules.d/99-umlaut.rules
sudo udevadm control --reload-rules
echo -e "${GREEN}✓ Removed system files and udev rules${NC}"

echo -e "\n${YELLOW}Removing user files...${NC}"
rm -f ~/.config/systemd/user/umlaut.service
rm -f ~/.config/autostart/umlaut-applet.desktop
echo -e "${GREEN}✓ Removed service and autostart entries${NC}"

# Ask before removing user config (contains user sequences)
echo ""
read -rp "Remove user config directory (~/.config/umlaut/)? This deletes your sequences. [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    rm -rf ~/.config/umlaut
    echo -e "${GREEN}✓ Removed user config${NC}"
else
    echo -e "${YELLOW}  Kept ~/.config/umlaut/${NC}"
fi

echo ""
read -rp "Remove $USER from the 'input' group? Only do this if Ümlaut added you to it. [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    sudo gpasswd -d "$USER" input 2>/dev/null && \
        echo -e "${GREEN}✓ Removed from input group (re-login to take effect)${NC}" || \
        echo -e "${YELLOW}  Could not remove from input group (may not have been a member)${NC}"
fi

systemctl --user daemon-reload 2>/dev/null || true

echo -e "\n${GREEN}Umlaut uninstalled.${NC}"
