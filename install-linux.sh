#!/bin/sh
# Set up Wardrive Uploader on Debian, Raspberry Pi OS or DietPi. Run from the repo folder.
#   sudo ./install-linux.sh        then        ./run-linux.sh
set -e
cd "$(dirname "$0")"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo: it installs packages and a udev rule." >&2
    exit 1
fi

# python3-gi + the AppIndicator typelib give the tray icon; the rest are the radios' system side.
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-gi gir1.2-ayatanaappindicator3-0.1 \
    libusb-1.0-0 bluez iw git xdg-utils

# The venv sees system packages so it can use python3-gi, which pip cannot build quickly on a Pi.
[ -d .venv ] || python3 -m venv --system-site-packages .venv
.venv/bin/pip install -q -r requirements.txt

# Lets a non-root user in plugdev open the monitor-mode adapters and the RTL-SDR
# (the job Zadig does on Windows). Root needs no rule.
cat > /etc/udev/rules.d/60-wardrive-uploader.rules <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", ATTR{idProduct}=="761[02]", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="0cf3", ATTR{idProduct}=="9271", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="0cf3", ATTR{idProduct}=="1006", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="283[289]", MODE="0660", GROUP="plugdev"
EOF
udevadm control --reload-rules 2>/dev/null || true

echo "Done. Start it from your desktop session with ./run-linux.sh"
echo "Wi-Fi scans are only requested when the app runs as root; otherwise it reads what the system already scanned."
