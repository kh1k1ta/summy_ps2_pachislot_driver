#!/usr/bin/env python3
"""
pachislot_driver.py — Userspace driver for summy モウジュウオウ pachislot controller
Cross-platform: Linux (evdev/uinput) and Windows (ViGEmBus/vgamepad)

ARCHITECTURE:
  [USB Device] --pyusb/libusb--> [This script] --uinput/ViGEmBus--> [Virtual Gamepad]

WINDOWS SETUP (one-time):
  1. Install Zadig (https://zadig.akeo.ie/)
     - Open Zadig, select "summy Pach-slot" (or show all devices if hidden)
     - Choose WinUSB as the driver → click "Replace Driver"
     WHY: Windows rejects the device's malformed descriptor and installs no driver
     (Code 43). Zadig replaces it with WinUSB, which lets libusb talk directly to
     the device, bypassing Windows' strict descriptor validation entirely.

  2. pip install pyusb libusb-package vgamepad
     WHY libusb-package: ships libusb-1.0.dll so pyusb can find the WinUSB device.
     WHY vgamepad: wraps ViGEmBus to create a virtual Xbox 360 controller.
     ViGEmBus itself is bundled with vgamepad — no separate install needed.

LINUX SETUP (one-time):
  sudo apt install python3-usb python3-evdev
  sudo modprobe uinput

USAGE:
  Windows:  python pachislot_driver.py          (as regular user, after Zadig)
  Linux:    sudo python3 pachislot_driver.py
"""

import sys
import signal
import logging
import platform
import usb.core
import usb.util
from dataclasses import dataclass
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"

# ─── Backend: Virtual Gamepad ─────────────────────────────────────────────────
# On Windows we use vgamepad (ViGEmBus → virtual Xbox 360 controller).
# On Linux we use evdev/uinput.

if IS_WINDOWS:
    try:
        import libusb_package          # ensures libusb-1.0.dll is loadable
        import usb.backend.libusb1
        import vgamepad as vg
        GAMEPAD_BACKEND = "vgamepad"
    except ImportError as _e:
        log.warning("vgamepad/libusb-package not found (%s) — print-only mode", _e)
        log.warning("Install with: pip install pyusb libusb-package vgamepad")
        GAMEPAD_BACKEND = "none"
else:
    try:
        import evdev
        from evdev import UInput, ecodes as ec
        GAMEPAD_BACKEND = "evdev"
    except ImportError:
        log.warning("evdev not found — print-only mode. Install: sudo apt install python3-evdev")
        GAMEPAD_BACKEND = "none"


# ─── Device Identity ──────────────────────────────────────────────────────────

VENDOR_ID  = 0x0A7B
PRODUCT_ID = 0xD001

# ─── USB Configuration ────────────────────────────────────────────────────────

ENDPOINT_ADDRESS = 0x81   # confirmed: only endpoint, Interrupt IN
PACKET_SIZE      = 4      # confirmed: device returns 4 bytes per packet
READ_TIMEOUT_MS  = 100

# ─── Button Map ───────────────────────────────────────────────────────────────
# (byte_index, bit_mask) → logical button name
# Confirmed from probe_device.py:
#   rest state = 00 00 00 00
#   byte[1] holds START, byte[2] holds all other buttons

BUTTON_MAP = {
    (2, 0x01): "A",       # ONE_BET
    (2, 0x04): "B",       # MAX_BET
    (2, 0x80): "LB",      # LEVER
    (2, 0x10): "X",       # STOP 1
    (2, 0x20): "Y",       # STOP 2
    (2, 0x40): "RB",      # STOP 3
    (2, 0x08): "BACK",    # COIN
    (1, 0x20): "START",   # START
}

INVERTED_POLARITY = False  # bit=1 means pressed (normal for this device)

# Maps our logical names → evdev key codes (Linux)
EVDEV_MAP = {
    "A":     "BTN_A",
    "B":     "BTN_B",
    "X":     "BTN_X",
    "Y":     "BTN_Y",
    "LB":    "BTN_TL",
    "RB":    "BTN_TR",
    "BACK":  "BTN_SELECT",
    "START": "BTN_START",
}

# Maps our logical names → vgamepad Xbox 360 button constants (Windows)
VGAMEPAD_MAP = {
    "A":     "XUSB_GAMEPAD_A",
    "B":     "XUSB_GAMEPAD_B",
    "X":     "XUSB_GAMEPAD_X",
    "Y":     "XUSB_GAMEPAD_Y",
    "LB":    "XUSB_GAMEPAD_LEFT_SHOULDER",
    "RB":    "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "BACK":  "XUSB_GAMEPAD_BACK",
    "START": "XUSB_GAMEPAD_START",
}


# ─── Virtual Gamepad Creation ─────────────────────────────────────────────────

def create_virtual_gamepad():
    if GAMEPAD_BACKEND == "vgamepad":
        try:
            gamepad = vg.VX360Gamepad()
            log.info("Virtual Xbox 360 gamepad created via ViGEmBus")
            return gamepad
        except Exception as ex:
            log.error("Failed to create vgamepad: %s", ex)
            log.error("Make sure ViGEmBus is installed (bundled with vgamepad pip package).")
            return None

    elif GAMEPAD_BACKEND == "evdev":
        button_codes = []
        for btn_name in EVDEV_MAP.values():
            code = getattr(ec, btn_name, None)
            if code is not None:
                button_codes.append(code)
        try:
            ui = UInput(
                events={ec.EV_KEY: button_codes},
                name="summy Pachislot Controller",
                vendor=VENDOR_ID,
                product=PRODUCT_ID,
                version=0x0001,
            )
            log.info("Virtual gamepad created via uinput → %s", ui.device.path)
            return ui
        except PermissionError:
            log.error("uinput needs root. Run: sudo python3 %s", sys.argv[0])
            return None
        except Exception as ex:
            log.error("Failed to create uinput device: %s", ex)
            return None

    return None  # print-only mode


# ─── USB Device Setup ─────────────────────────────────────────────────────────

def open_device() -> Optional[usb.core.Device]:
    # On Windows, tell pyusb to use the libusb1 backend (talks to WinUSB devices)
    backend = None
    if IS_WINDOWS and GAMEPAD_BACKEND != "none":
        backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID, backend=backend)
    if dev is None:
        log.error("Device not found (VID=%04x PID=%04x).", VENDOR_ID, PRODUCT_ID)
        if IS_WINDOWS:
            log.error("Did you run Zadig and install WinUSB for this device?")
        return None

    log.info("Found device: bus=%d address=%d", dev.bus, dev.address)

    # Detach kernel driver — Linux only. Windows has no equivalent concept,
    # and calling this on Windows raises NotImplementedError.
    if not IS_WINDOWS:
        for iface_num in range(4):
            try:
                if dev.is_kernel_driver_active(iface_num):
                    dev.detach_kernel_driver(iface_num)
                    log.info("Detached kernel driver from interface %d", iface_num)
            except usb.core.USBError:
                pass

    # Set configuration — may fail on Windows if the descriptor is still seen as
    # malformed even through WinUSB; raw fallback handles that case.
    try:
        dev.set_configuration(1)
        log.info("Configuration set")
    except usb.core.USBError as ex:
        log.warning("set_configuration failed (%s), trying raw control transfer...", ex)
        try:
            dev.ctrl_transfer(0x00, 0x09, 1, 0, None, timeout=2000)
            log.info("Raw SET_CONFIGURATION(1) succeeded")
        except usb.core.USBError as ex2:
            log.error("Configuration failed: %s", ex2)
            return None

    try:
        usb.util.claim_interface(dev, 0)
        log.info("Interface 0 claimed")
    except usb.core.USBError as ex:
        log.warning("claim_interface: %s (may still work)", ex)

    return dev


# ─── Protocol Decoder ─────────────────────────────────────────────────────────

@dataclass
class ControllerState:
    raw_bytes: bytes
    buttons: dict  # logical_name → bool

    def __repr__(self):
        pressed = [k for k, v in self.buttons.items() if v]
        return f"ControllerState(pressed={pressed}, raw={self.raw_bytes.hex(' ')})"


def decode_packet(data: bytes) -> ControllerState:
    buttons = {}
    for (byte_idx, bit_mask), btn_name in BUTTON_MAP.items():
        if byte_idx >= len(data):
            buttons[btn_name] = False
            continue
        bit_set = bool(data[byte_idx] & bit_mask)
        buttons[btn_name] = (not bit_set) if INVERTED_POLARITY else bit_set
    return ControllerState(raw_bytes=bytes(data), buttons=buttons)


# ─── Input Injection ──────────────────────────────────────────────────────────

def emit_gamepad_state(gamepad, state: ControllerState, prev_state: Optional[ControllerState]) -> None:
    if gamepad is None:
        # Print-only mode
        if prev_state is None or state.buttons != prev_state.buttons:
            pressed = [k for k, v in state.buttons.items() if v]
            log.info("Buttons: %-30s  raw: %s", str(pressed) if pressed else "(none)", state.raw_bytes.hex(" "))
        return

    if GAMEPAD_BACKEND == "vgamepad":
        # vgamepad is state-based: update all changed buttons then call update() once.
        changed = False
        for btn_name, pressed in state.buttons.items():
            prev_pressed = prev_state.buttons.get(btn_name, False) if prev_state else False
            if pressed == prev_pressed:
                continue
            xusb_name = VGAMEPAD_MAP.get(btn_name)
            if xusb_name is None:
                continue
            xusb_button = getattr(vg.XUSB_BUTTON, xusb_name)
            if pressed:
                gamepad.press_button(button=xusb_button)
            else:
                gamepad.release_button(button=xusb_button)
            log.debug("%s %s", btn_name, "PRESSED" if pressed else "released")
            changed = True
        if changed:
            gamepad.update()  # sends the updated state to ViGEmBus in one shot

    elif GAMEPAD_BACKEND == "evdev":
        changed = False
        for btn_name, pressed in state.buttons.items():
            prev_pressed = prev_state.buttons.get(btn_name, False) if prev_state else False
            if pressed == prev_pressed:
                continue
            evdev_name = EVDEV_MAP.get(btn_name)
            if evdev_name is None:
                continue
            code = getattr(ec, evdev_name, None)
            if code is not None:
                gamepad.write(ec.EV_KEY, code, 1 if pressed else 0)
                log.debug("%s %s", btn_name, "PRESSED" if pressed else "released")
                changed = True
        if changed:
            gamepad.syn()


# ─── Main Loop ───────────────────────────────────────────────────────────────

def run(dev: usb.core.Device, gamepad) -> None:
    log.info("Reading from endpoint %#04x, %d bytes/packet. Ctrl+C to stop.\n", ENDPOINT_ADDRESS, PACKET_SIZE)

    prev_state: Optional[ControllerState] = None
    error_count = 0
    MAX_ERRORS = 10

    while True:
        try:
            raw = dev.read(ENDPOINT_ADDRESS, PACKET_SIZE, timeout=READ_TIMEOUT_MS)
            state = decode_packet(bytes(raw))
            emit_gamepad_state(gamepad, state, prev_state)
            prev_state = state
            error_count = 0

        except usb.core.USBError as ex:
            msg = str(ex).lower()
            if "timeout" in msg:
                continue
            elif "no such device" in msg or "disconnect" in msg:
                log.error("Device disconnected.")
                break
            elif "pipe" in msg or "stall" in msg:
                log.warning("Endpoint stall — clearing halt...")
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
            log.info("Stopped.")
            break


def main():
    print("=" * 60)
    print(" pachislot_driver.py — summy モウジュウオウ controller")
    print(f" Platform: {platform.system()}  Backend: {GAMEPAD_BACKEND}")
    print(f" VID={VENDOR_ID:#06x}  PID={PRODUCT_ID:#06x}  EP={ENDPOINT_ADDRESS:#04x}")
    print("=" * 60, "\n")

    dev = open_device()
    if dev is None:
        sys.exit(1)

    gamepad = create_virtual_gamepad()

    def cleanup(signum, frame):
        usb.util.dispose_resources(dev)
        if gamepad and GAMEPAD_BACKEND == "evdev":
            gamepad.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)

    try:
        run(dev, gamepad)
    finally:
        usb.util.dispose_resources(dev)
        if gamepad and GAMEPAD_BACKEND == "evdev":
            gamepad.close()
        log.info("Done.")


if __name__ == "__main__":
    main()
