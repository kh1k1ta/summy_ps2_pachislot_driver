#!/usr/bin/env python3
"""
pachislot_driver.py — Userspace driver for summy モウジュウオウ pachislot controller

ARCHITECTURE OVERVIEW:
  [USB Device] --pyusb/libusb--> [This script] --uinput--> [Virtual Gamepad] --> Games/Apps

  No kernel driver needed. This is a fully userspace solution:
  - pyusb reads raw interrupt packets from the controller
  - We decode the button/lever state from the bytes
  - We inject those states into a virtual gamepad via Linux uinput

  On the Windows side later, the same approach works with:
  - pyusb + WinUSB (via Zadig) for the USB reading
  - ViGEmBus or vJoy for the virtual gamepad

REQUIREMENTS:
  pip install pyusb evdev
  sudo apt install libusb-1.0-0

  The uinput kernel module must be loaded:
    sudo modprobe uinput

USAGE:
  sudo python3 pachislot_driver.py

  --- IMPORTANT ---
  You MUST run probe_device.py first to discover the working endpoint address
  and the button bit layout. Then update BUTTON_MAP below to match.
  The defaults here are educated guesses based on common PS2 controller layouts.

BUTTON MAPPING (update these after running probe_device.py):
  ENDPOINT_ADDRESS: which USB endpoint to read from
  PACKET_SIZE:      how many bytes per packet
  BUTTON_MAP:       maps (byte_index, bit_mask) → gamepad button name
"""

import usb.core
import usb.util
import struct
import sys
import time
import signal
import logging
from dataclasses import dataclass
from typing import Optional

# Try to import evdev for uinput virtual gamepad
try:
    import evdev
    from evdev import UInput, ecodes as e
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False
    print("[WARNING] evdev not installed. Running in print-only mode.")
    print("          Install with: pip install evdev")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ─── Device Identity ──────────────────────────────────────────────────────────

VENDOR_ID  = 0x0A7B
PRODUCT_ID = 0xD001

# ─── USB Configuration ────────────────────────────────────────────────────────
# Update ENDPOINT_ADDRESS after running probe_device.py.
# 0x81 = Endpoint 1 IN (most common for PS2-style controllers)

ENDPOINT_ADDRESS = 0x81   # confirmed from probe_device.py output
PACKET_SIZE      = 4      # confirmed from probe: device returns exactly 4 bytes
READ_TIMEOUT_MS  = 100    # 100ms read timeout; controller typically reports every 8–16ms

# ─── Button Map ───────────────────────────────────────────────────────────────
# Format: (byte_index, bit_mask): gamepad_button_constant
#
# This is a GUESS based on typical PS2 controller layouts.
# Run probe_device.py and press each button one at a time to find the real layout.
# Typical pachislot buttons: BET, MAX BET, START (lever), STOP1, STOP2, STOP3
#
# evdev gamepad buttons: BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START
#
# Example layout (replace with real values from probe):
#   byte 0 bit 0 → BET      → BTN_A
#   byte 0 bit 1 → MAX BET  → BTN_B
#   byte 0 bit 2 → LEVER    → BTN_TL
#   byte 0 bit 3 → STOP 1   → BTN_X
#   byte 0 bit 4 → STOP 2   → BTN_Y
#   byte 0 bit 5 → STOP 3   → BTN_TR
#   byte 0 bit 6 → SELECT   → BTN_SELECT
#   byte 0 bit 7 → START    → BTN_START

BUTTON_MAP = {
    (2, 0x01): "BTN_A",       # ONE_BET
    (2, 0x04): "BTN_B",       # MAX_BET
    (2, 0x80): "BTN_TL",      # LEVER
    (2, 0x10): "BTN_X",       # STOP 1
    (2, 0x20): "BTN_Y",       # STOP 2
    (2, 0x40): "BTN_TR",      # STOP 3
    (2, 0x08): "BTN_SELECT",  # COIN
    (1, 0x20): "BTN_START",   # START
}

# If the protocol is inverted (bit=0 means pressed, bit=1 means released)
# set this to True. Common for some PS2 controllers.
INVERTED_POLARITY = False


# ─── Virtual Gamepad ─────────────────────────────────────────────────────────

def create_virtual_gamepad() -> Optional["UInput"]:
    """
    Creates a virtual gamepad using Linux uinput.

    WHY UINPUT:
      uinput is a kernel facility that lets userspace programs create virtual
      input devices. The OS sees it as a real gamepad — games, Steam, etc.
      all work without any extra configuration. No kernel driver needed.

    The virtual device will appear as something like /dev/input/event<N>
    and will show up in `cat /proc/bus/input/devices`.
    """
    if not EVDEV_AVAILABLE:
        return None

    # Collect all button codes we need
    button_codes = []
    for btn_name in BUTTON_MAP.values():
        code = getattr(e, btn_name, None)
        if code is not None:
            button_codes.append(code)

    capabilities = {
        e.EV_KEY: button_codes,
        # Uncomment to add analog axes if the lever needs analog output:
        # e.EV_ABS: [
        #     (e.ABS_X, evdev.AbsInfo(value=0, min=-32767, max=32767, fuzz=0, flat=0, resolution=0)),
        # ],
    }

    try:
        ui = UInput(
            events=capabilities,
            name="summy Pachislot Controller",
            vendor=VENDOR_ID,
            product=PRODUCT_ID,
            version=0x0001,
        )
        log.info(f"Virtual gamepad created: {ui.name} → {ui.device.path}")
        return ui
    except PermissionError:
        log.error("Cannot create uinput device — need root or membership in 'input' group.")
        log.error("Run with sudo, or: sudo usermod -aG input $USER && newgrp input")
        return None
    except Exception as ex:
        log.error(f"Failed to create virtual gamepad: {ex}")
        return None


# ─── USB Device Setup ─────────────────────────────────────────────────────────

def open_device() -> Optional[usb.core.Device]:
    """
    Opens the USB device.

    WHY WE DETACH THE KERNEL DRIVER:
      When a USB device is plugged in, Linux may automatically attach a kernel
      driver to it (even if it's just the generic usbhid driver guessing). We
      must detach it so libusb can claim exclusive access to the interface.

    WHY set_configuration() MAY FAIL:
      The device has a malformed Configuration Descriptor. On Linux, set_configuration()
      may still succeed (Linux is lenient), but if it fails we send a raw
      SET_CONFIGURATION control transfer as a fallback.
    """
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        log.error("Device not found (VID=%04x PID=%04x). Is it plugged in?", VENDOR_ID, PRODUCT_ID)
        return None

    log.info("Found device: bus=%d address=%d", dev.bus, dev.address)

    # Detach any kernel driver holding the interface
    for iface_num in range(4):
        try:
            if dev.is_kernel_driver_active(iface_num):
                dev.detach_kernel_driver(iface_num)
                log.info("Detached kernel driver from interface %d", iface_num)
        except usb.core.USBError:
            pass

    # Try to set configuration
    try:
        dev.set_configuration(1)
        log.info("Configuration set successfully")
    except usb.core.USBError as ex:
        log.warning("set_configuration failed (%s), attempting raw control transfer...", ex)
        try:
            # bmRequestType=0x00 (Host→Device, Standard, Device), bRequest=0x09 (SET_CONFIGURATION)
            dev.ctrl_transfer(0x00, 0x09, 1, 0, None, timeout=2000)
            log.info("Raw SET_CONFIGURATION(1) succeeded")
        except usb.core.USBError as ex2:
            log.error("Configuration failed entirely: %s", ex2)
            log.error("Try running: sudo dmesg | grep -i usb | tail -30")
            return None

    # Claim interface 0
    try:
        usb.util.claim_interface(dev, 0)
        log.info("Interface 0 claimed")
    except usb.core.USBError as ex:
        log.warning("Could not explicitly claim interface 0: %s (may still work)", ex)

    return dev


# ─── Protocol Decoder ─────────────────────────────────────────────────────────

@dataclass
class ControllerState:
    """Decoded controller state from one USB packet."""
    raw_bytes: bytes
    buttons: dict  # button_name → bool (True = pressed)

    def __repr__(self):
        pressed = [k for k, v in self.buttons.items() if v]
        return f"ControllerState(pressed={pressed}, raw={self.raw_bytes.hex(' ')})"


def decode_packet(data: bytes) -> ControllerState:
    """
    Decodes raw USB packet bytes into a ControllerState.

    This is where you apply what you learned from probe_device.py.
    The default implementation assumes each button is one bit in the first byte,
    which is the most common layout for simple controllers.

    If INVERTED_POLARITY is True, bit=0 means pressed (common for pull-up logic).
    """
    buttons = {}
    for (byte_idx, bit_mask), btn_name in BUTTON_MAP.items():
        if byte_idx >= len(data):
            buttons[btn_name] = False
            continue
        bit_set = bool(data[byte_idx] & bit_mask)
        pressed = (not bit_set) if INVERTED_POLARITY else bit_set
        buttons[btn_name] = pressed
    return ControllerState(raw_bytes=bytes(data), buttons=buttons)


# ─── Input Injection ──────────────────────────────────────────────────────────

def emit_gamepad_state(ui: Optional["UInput"], state: ControllerState, prev_state: Optional[ControllerState]) -> None:
    """
    Sends button events to the virtual gamepad for any buttons that changed state.

    WHY ONLY CHANGED BUTTONS:
      uinput expects key events (press/release), not absolute state. We diff
      the current and previous state and emit events only for changes.
      This also avoids flooding the input system with redundant events.
    """
    if ui is None:
        # Print-only mode
        pressed = [k for k, v in state.buttons.items() if v]
        if prev_state is None or state.buttons != prev_state.buttons:
            log.info("Button state: %s | raw: %s", pressed if pressed else "(none)", state.raw_bytes.hex(" "))
        return

    changed = False
    for btn_name, pressed in state.buttons.items():
        prev_pressed = prev_state.buttons.get(btn_name, False) if prev_state else False
        if pressed != prev_pressed:
            code = getattr(e, btn_name, None)
            if code is not None:
                ui.write(e.EV_KEY, code, 1 if pressed else 0)
                log.debug("%s %s", btn_name, "PRESSED" if pressed else "released")
                changed = True

    if changed:
        ui.syn()  # Flush all pending events to the kernel in one sync


# ─── Main Loop ───────────────────────────────────────────────────────────────

def run(dev: usb.core.Device, ui: Optional["UInput"]) -> None:
    """
    Main read loop.

    Reads interrupt packets from the device endpoint in a tight loop.
    USB interrupt endpoints have a polling interval (bInterval) — the device
    sends data at that rate. We use a short timeout so we can catch KeyboardInterrupt.
    """
    log.info("Starting read loop on endpoint %#04x (packet size %d)", ENDPOINT_ADDRESS, PACKET_SIZE)
    log.info("Press Ctrl+C to stop.\n")

    prev_state: Optional[ControllerState] = None
    error_count = 0
    MAX_ERRORS = 10

    while True:
        try:
            raw = dev.read(ENDPOINT_ADDRESS, PACKET_SIZE, timeout=READ_TIMEOUT_MS)
            state = decode_packet(bytes(raw))
            emit_gamepad_state(ui, state, prev_state)
            prev_state = state
            error_count = 0

        except usb.core.USBError as ex:
            msg = str(ex).lower()
            if "timeout" in msg:
                # Normal — no data from idle device
                continue
            elif "no such device" in msg or "disconnected" in msg:
                log.error("Device disconnected!")
                break
            elif "pipe" in msg or "stall" in msg:
                # Endpoint stalled — clear the stall and retry
                log.warning("Endpoint stall detected — clearing halt...")
                try:
                    dev.clear_halt(ENDPOINT_ADDRESS)
                except Exception:
                    pass
            else:
                error_count += 1
                log.warning("USB error (%d/%d): %s", error_count, MAX_ERRORS, ex)
                if error_count >= MAX_ERRORS:
                    log.error("Too many errors — stopping.")
                    break

        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break


def main():
    print("=" * 60)
    print(" pachislot_driver.py — summy モウジュウオウ controller")
    print(f" VID={VENDOR_ID:#06x}  PID={PRODUCT_ID:#06x}")
    print(f" Endpoint={ENDPOINT_ADDRESS:#04x}  PacketSize={PACKET_SIZE}")
    print("=" * 60)

    if not EVDEV_AVAILABLE:
        print("\n[!] evdev not available — running in print-only (debug) mode")
        print("    Install with: pip install evdev\n")

    # Open USB device
    dev = open_device()
    if dev is None:
        sys.exit(1)

    # Create virtual gamepad
    ui = create_virtual_gamepad()

    # Handle Ctrl+C / SIGTERM gracefully
    def cleanup(signum, frame):
        log.info("Signal received — cleaning up...")
        usb.util.dispose_resources(dev)
        if ui:
            ui.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)

    try:
        run(dev, ui)
    finally:
        usb.util.dispose_resources(dev)
        if ui:
            ui.close()
        log.info("Resources released.")


if __name__ == "__main__":
    main()
