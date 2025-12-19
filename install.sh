#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Fan Control Installation ===${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (sudo ./install.sh)${NC}"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/fan-control"

# Check for nct6779
echo -e "${YELLOW}Checking for nct6779 sensor...${NC}"
if ! grep -q nct6779 /sys/class/hwmon/hwmon*/name 2>/dev/null; then
    echo -e "${RED}nct6779 sensor not found!${NC}"
    echo "Make sure the nct6775 module is loaded:"
    echo "  sudo modprobe nct6775"
    echo "  echo 'nct6775' | sudo tee /etc/modules-load.d/nct6775.conf"
    exit 1
fi
echo -e "${GREEN}Found nct6779 sensor${NC}"

# Install system dependencies
echo -e "${YELLOW}Installing system dependencies...${NC}"
apt-get update
apt-get install -y python3 python3-venv python3-pip nodejs npm lm-sensors

# Create install directory
echo -e "${YELLOW}Creating installation directory...${NC}"
mkdir -p "$INSTALL_DIR"

# Copy backend files
echo -e "${YELLOW}Copying backend files...${NC}"
cp -r "$SCRIPT_DIR/backend" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/backend/fan_helper.py"

# Create Python virtual environment
echo -e "${YELLOW}Setting up Python virtual environment...${NC}"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt"

# Build frontend
echo -e "${YELLOW}Building frontend...${NC}"
cd "$SCRIPT_DIR/frontend"
npm install
npm run build

# Copy built frontend
cp -r "$SCRIPT_DIR/frontend/dist" "$INSTALL_DIR/frontend/"

# Setup sudoers for fan_helper (passwordless)
echo -e "${YELLOW}Setting up sudo permissions...${NC}"
SUDOERS_FILE="/etc/sudoers.d/fan-control"
echo "# Allow fan-control service to run fan_helper without password" > "$SUDOERS_FILE"
echo "ALL ALL=(ALL) NOPASSWD: /usr/bin/python3 $INSTALL_DIR/backend/fan_helper.py *" >> "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"

# Install systemd service
echo -e "${YELLOW}Installing systemd service...${NC}"
cp "$SCRIPT_DIR/fan-control.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable fan-control
systemctl start fan-control

# Check status
sleep 2
if systemctl is-active --quiet fan-control; then
    echo -e "${GREEN}=== Installation Complete ===${NC}"
    echo ""
    echo "Fan Control is running!"
    echo "Access the web interface at: http://$(hostname -I | awk '{print $1}'):8000"
    echo ""
    echo "Commands:"
    echo "  sudo systemctl status fan-control   # Check status"
    echo "  sudo systemctl restart fan-control  # Restart service"
    echo "  sudo journalctl -u fan-control -f   # View logs"
    echo ""
else
    echo -e "${RED}Service failed to start. Check logs:${NC}"
    echo "  sudo journalctl -u fan-control -n 50"
    exit 1
fi
