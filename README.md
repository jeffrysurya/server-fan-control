# Fan Control Web App

Web-based fan control for systems with NCT6779 (ASRock B450 Steel Legend and similar motherboards).

## Features

- **Global Mode Toggle**: Switch between BIOS automatic control and manual curve control
- **Real-time Monitoring**: Live temperature and RPM updates via WebSocket (1s interval)
- **Custom Fan Curves**: 5-point temperature-based PWM curves per fan
- **Persistent Settings**: Saved curves are restored on reboot
- **Custom Fan Names**: Rename fans for easier identification

## Requirements

- Ubuntu Server (tested on 22.04/24.04)
- Motherboard with NCT6779 Super I/O chip (ASRock B450 series, etc.)
- Python 3.10+
- Node.js 18+

## Quick Install

```bash
# Clone or copy the fan-control directory to your server
cd fan-control

# Make scripts executable
chmod +x install.sh uninstall.sh

# Run installer as root
sudo ./install.sh
```

The installer will:

1. Check for NCT6779 sensor
2. Install Python and Node.js dependencies
3. Build the React frontend
4. Setup passwordless sudo for the helper script
5. Install and start the systemd service

## Manual Installation

If you prefer manual setup:

```bash
# 1. Install dependencies
sudo apt install python3 python3-venv python3-pip nodejs npm lm-sensors

# 2. Create install directory
sudo mkdir -p /opt/fan-control
sudo cp -r backend /opt/fan-control/

# 3. Setup Python venv
sudo python3 -m venv /opt/fan-control/venv
sudo /opt/fan-control/venv/bin/pip install -r /opt/fan-control/backend/requirements.txt

# 4. Build frontend
cd frontend
npm install
npm run build
sudo mkdir -p /opt/fan-control/frontend
sudo cp -r dist /opt/fan-control/frontend/

# 5. Setup sudoers (create /etc/sudoers.d/fan-control)
# ALL ALL=(ALL) NOPASSWD: /usr/bin/python3 /opt/fan-control/backend/fan_helper.py *

# 6. Install systemd service
sudo cp fan-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fan-control
```

## Usage

Access the web interface at `http://YOUR_SERVER_IP:8001`

### Modes

- **Auto (BIOS)**: Fans controlled by motherboard SmartFan IV (mode 5)
- **Manual Curve**: Custom curves applied via OS (mode 1)

### Editing Curves

1. Click the edit icon (pencil) on any fan card
2. Adjust temperature and PWM percentage for each of the 5 points
3. Curves must be monotonically increasing (higher temp = higher PWM)
4. Click Save - changes are applied immediately if in Manual mode

### API Endpoints

| Endpoint        | Method    | Description                                           |
| --------------- | --------- | ----------------------------------------------------- |
| `/api/status`   | GET       | Current fan status, temps, RPM                        |
| `/api/config`   | GET       | Current configuration                                 |
| `/api/mode`     | POST      | Set mode (`{"mode": "auto"}` or `{"mode": "manual"}`) |
| `/api/curve`    | POST      | Set fan curve                                         |
| `/api/fan_name` | POST      | Set fan name                                          |
| `/ws`           | WebSocket | Real-time status updates                              |

## Configuration

Config is stored in `/opt/fan-control/backend/config.json`:

```json
{
  "mode": "manual",
  "curves": {
    "1": [
      {"point": 1, "temp": 30, "pwm": 50},
      {"point": 2, "temp": 40, "pwm": 80},
      ...
    ]
  },
  "fan_names": {
    "1": "CPU Fan",
    "2": "Rear Exhaust"
  }
}
```

## Troubleshooting

### Sensor not found

```bash
# Load the kernel module
sudo modprobe nct6775

# Make it persistent
echo "nct6775" | sudo tee /etc/modules-load.d/nct6775.conf

# Verify
cat /sys/class/hwmon/hwmon*/name | grep nct6779
```

### Service won't start

```bash
# Check logs
sudo journalctl -u fan-control -n 100

# Test helper script
sudo python3 /opt/fan-control/backend/fan_helper.py get_status
```

### Permission denied

Ensure sudoers file exists and has correct permissions:

```bash
sudo cat /etc/sudoers.d/fan-control
# Should contain:
# ALL ALL=(ALL) NOPASSWD: /usr/bin/python3 /opt/fan-control/backend/fan_helper.py *
```

### Fans not responding

Some motherboards lock fan control in BIOS. Check:

1. BIOS → H/W Monitor → Fan-Tastic Tuning
2. Set fans to "Full Speed" or "Manual" to release OS control

## Uninstall

```bash
sudo ./uninstall.sh
```

This will:

- Stop and remove the systemd service
- Remove sudo permissions
- Reset fans to BIOS control
- Optionally remove `/opt/fan-control`

## Security Notes

- The service runs as root (required for sysfs writes)
- Intended for local network / VPN access only
- No authentication built-in - use firewall rules or reverse proxy with auth if exposing externally

## License

MIT
