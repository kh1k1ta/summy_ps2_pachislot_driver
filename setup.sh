#!/usr/bin/env bash
# setup.sh — one-time setup for pachislot driver
set -e

echo "=== pachislot controller setup ==="

# 1. Install Python dependencies via apt
# WHY apt and not pip: modern Debian/Ubuntu uses "externally managed" Python (PEP 668).
# pip installs go to ~/.local and are invisible to root (needed for sudo python3).
# apt installs to the system Python that both user and root share.
sudo apt install -y python3-usb python3-evdev

# 2. Load the uinput kernel module (needed for virtual gamepad)
sudo modprobe uinput

# 3. Create a udev rule so the device is accessible without sudo
#    Replace 0a7b/d001 with your actual VID/PID if different.
UDEV_RULE='SUBSYSTEM=="usb", ATTRS{idVendor}=="0a7b", ATTRS{idProduct}=="d001", MODE="0666", GROUP="plugdev"'
UDEV_FILE="/etc/udev/rules.d/99-pachislot.rules"

echo "Writing udev rule to $UDEV_FILE ..."
echo "$UDEV_RULE" | sudo tee "$UDEV_FILE"
sudo udevadm control --reload-rules
sudo udevadm trigger

echo ""
echo "Done. Next steps:"
echo "  1. Plug in the controller"
echo "  2. Run:  python3 probe_device.py      (find endpoint address + button layout)"
echo "  3. Edit pachislot_driver.py with what you learned"
echo "  4. Run:  python3 pachislot_driver.py  (virtual gamepad active!)"
echo ""
echo "Useful debug commands:"
echo "  lsusb -v -d 0a7b:d001         # show device descriptors"
echo "  sudo dmesg | tail -20          # kernel USB messages"
echo "  cat /proc/bus/input/devices    # list input devices incl. virtual gamepad"
