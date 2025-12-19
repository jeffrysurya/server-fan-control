#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}=== Fan Control Uninstallation ===${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (sudo ./uninstall.sh)${NC}"
    exit 1
fi

# Stop and disable service
echo "Stopping service..."
systemctl stop fan-control 2>/dev/null || true
systemctl disable fan-control 2>/dev/null || true

# Remove systemd service
echo "Removing systemd service..."
rm -f /etc/systemd/system/fan-control.service
systemctl daemon-reload

# Remove sudoers file
echo "Removing sudo permissions..."
rm -f /etc/sudoers.d/fan-control

# Reset fans to BIOS control
echo "Resetting fans to BIOS control..."
HWMON=$(grep -l nct6779 /sys/class/hwmon/hwmon*/name 2>/dev/null | head -1 | xargs dirname)
if [ -n "$HWMON" ]; then
    for i in 1 2 3 4 5; do
        echo 5 > "$HWMON/pwm${i}_enable" 2>/dev/null || true
    done
    echo -e "${GREEN}Fans reset to BIOS control${NC}"
fi

# Ask about removing installation directory
read -p "Remove /opt/fan-control directory? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -rf /opt/fan-control
    echo "Installation directory removed"
fi

echo -e "${GREEN}=== Uninstallation Complete ===${NC}"
