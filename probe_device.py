#!/usr/bin/env python3
"""
probe_device.py — USB probe/analysis tool for summy モウジュウオウ pachislot controller

WHY THIS SCRIPT EXISTS:
  Windows rejects the device at the Configuration Descriptor stage (CONFIGURATION_DESCRIPTOR_VALIDATION_FAILURE).
  This means no endpoints are ever opened on Windows and Wireshark sees nothing.
  Linux is more forgiving — it may enumerate the device even with the bad descriptor.

  This script does three things:
  1. Manually reads the raw Configuration Descriptor bytes (bypassing pyusb's validation)
     so we can see exactly what's malformed and what endpoints actually exist.
  2. Tries to claim the interface and brute-force read from common endpoint addresses.
  3. Prints raw hex output as you press buttons, so you can learn the protocol.

REQUIREMENTS:
  pip install pyusb
  sudo apt install libusb-1.0-0  (usually already present)

USAGE:
  sudo python3 probe_device.py
  (needs root or a udev rule to access the USB device without sudo)
"""

import usb.core
import usb.util
import usb.backend.libusb1
import struct
import sys
import time

VENDOR_ID  = 0x0A7B
PRODUCT_ID = 0xD001

# USB standard request constants
USB_REQ_GET_DESCRIPTOR     = 0x06
USB_DT_DEVICE              = 0x01
USB_DT_CONFIG              = 0x02
USB_DT_STRING              = 0x03
USB_RECIP_DEVICE           = 0x00
USB_DIR_IN                 = 0x80

# Common interrupt endpoint addresses to brute-force
# PS2 controllers almost always use interrupt IN endpoints
CANDIDATE_ENDPOINTS = [0x81, 0x82, 0x83, 0x84, 0x01, 0x02, 0x03, 0x04]

def hexdump(data: bytes, label: str = "") -> None:
    if label:
        print(f"\n=== {label} ===")
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part  = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {i:04X}:  {hex_part:<48}  {ascii_part}")


def read_raw_descriptor(dev, desc_type: int, index: int = 0, length: int = 255) -> bytes:
    """
    Reads a USB descriptor using a raw control transfer, bypassing pyusb's
    descriptor parser/validator. This is how we read the "broken" config descriptor
    that Windows refuses to parse.

    bmRequestType = 0x80 (Device-to-Host, Standard, Device recipient)
    bRequest      = GET_DESCRIPTOR (0x06)
    wValue        = (descriptor_type << 8) | index
    wIndex        = 0 (language ID for strings, 0 for others)
    """
    try:
        data = dev.ctrl_transfer(
            bmRequestType = USB_DIR_IN | USB_RECIP_DEVICE,
            bRequest      = USB_REQ_GET_DESCRIPTOR,
            wValue        = (desc_type << 8) | index,
            wIndex        = 0,
            data_or_wLength = length,
            timeout       = 2000
        )
        return bytes(data)
    except usb.core.USBError as e:
        return b""


def parse_config_descriptor(raw: bytes) -> None:
    """
    Manually walks the raw Configuration Descriptor to find interfaces and endpoints.
    WHY: pyusb may crash or raise an exception trying to parse a malformed descriptor.
    We do it by hand so we can tolerate malformed data and still find endpoints.

    USB descriptor structure: each descriptor starts with [bLength, bDescriptorType, ...]
      bDescriptorType 0x02 = CONFIGURATION
      bDescriptorType 0x04 = INTERFACE
      bDescriptorType 0x05 = ENDPOINT
    """
    print("\n--- Manual Configuration Descriptor Walk ---")
    if len(raw) < 4:
        print("  [!] Too short to be a valid config descriptor")
        return

    i = 0
    while i < len(raw):
        if i + 2 > len(raw):
            break
        bLength = raw[i]
        bType   = raw[i + 1]
        if bLength == 0:
            print(f"  [!] Zero-length descriptor at offset {i:#x} — stopping walk")
            break

        chunk = raw[i : i + bLength]

        if bType == 0x02:  # CONFIGURATION descriptor
            if len(chunk) >= 9:
                wTotalLength = struct.unpack_from("<H", chunk, 2)[0]
                bNumInterfaces = chunk[4]
                bConfigValue   = chunk[5]
                print(f"  [CONFIGURATION] wTotalLength={wTotalLength}  bNumInterfaces={bNumInterfaces}  bConfigValue={bConfigValue}")
                if wTotalLength != len(raw):
                    print(f"  [!] wTotalLength mismatch: descriptor says {wTotalLength}, we got {len(raw)} bytes "
                          f"← THIS IS LIKELY WHY WINDOWS REJECTS IT")

        elif bType == 0x04:  # INTERFACE descriptor
            if len(chunk) >= 9:
                bInterfaceNumber = chunk[2]
                bNumEndpoints    = chunk[4]
                bInterfaceClass  = chunk[5]
                bInterfaceSubClass = chunk[6]
                bInterfaceProtocol = chunk[7]
                print(f"  [INTERFACE]     bInterfaceNumber={bInterfaceNumber}  bNumEndpoints={bNumEndpoints}  "
                      f"class={bInterfaceClass:#04x}  subclass={bInterfaceSubClass:#04x}  protocol={bInterfaceProtocol:#04x}")

        elif bType == 0x05:  # ENDPOINT descriptor
            if len(chunk) >= 7:
                bEndpointAddress = chunk[2]
                bmAttributes     = chunk[3]
                wMaxPacketSize   = struct.unpack_from("<H", chunk, 4)[0]
                bInterval        = chunk[6]
                direction = "IN" if (bEndpointAddress & 0x80) else "OUT"
                xfer_type = ["Control", "Isochronous", "Bulk", "Interrupt"][bmAttributes & 0x03]
                print(f"  [ENDPOINT]      address={bEndpointAddress:#04x} ({direction})  "
                      f"type={xfer_type}  wMaxPacketSize={wMaxPacketSize}  bInterval={bInterval}")

        else:
            print(f"  [DESC type={bType:#04x}]  length={bLength}")

        i += bLength


def try_enumerate(dev) -> None:
    """
    Tries to set a configuration on the device.
    On Linux, set_configuration() may succeed even if the descriptor is slightly malformed,
    because Linux's USB core is more lenient during enumeration.

    We try configuration value 1 first (most common), then fall back to raw SET_CONFIGURATION
    control transfer if pyusb's abstraction fails.
    """
    print("\n--- Attempting Device Enumeration ---")

    # Detach any kernel driver that may have grabbed the device
    for iface in range(4):
        try:
            if dev.is_kernel_driver_active(iface):
                dev.detach_kernel_driver(iface)
                print(f"  Detached kernel driver from interface {iface}")
        except Exception:
            pass

    # Try set_configuration via pyusb
    try:
        dev.set_configuration(1)
        print("  [OK] set_configuration(1) succeeded via pyusb")
        return True
    except usb.core.USBError as e:
        print(f"  [!] set_configuration(1) failed: {e}")

    # Fallback: raw SET_CONFIGURATION control transfer
    # bmRequestType = 0x00 (Host-to-Device, Standard, Device), bRequest = 0x09 (SET_CONFIGURATION)
    try:
        dev.ctrl_transfer(0x00, 0x09, 1, 0, None, timeout=2000)
        print("  [OK] raw SET_CONFIGURATION(1) control transfer succeeded")
        return True
    except usb.core.USBError as e:
        print(f"  [!] raw SET_CONFIGURATION also failed: {e}")
        return False


def try_claim_interface(dev, interface: int = 0) -> bool:
    try:
        usb.util.claim_interface(dev, interface)
        print(f"  [OK] Claimed interface {interface}")
        return True
    except usb.core.USBError as e:
        print(f"  [!] Could not claim interface {interface}: {e}")
        return False


def brute_force_read(dev) -> None:
    """
    Tries to read from each candidate endpoint address in turn.
    PS2 controllers send data via Interrupt IN transfers, typically every 8-16ms.

    When a read succeeds, we print the raw bytes so you can:
    - See the packet size (tells us wMaxPacketSize)
    - Press buttons and compare how bytes change (tells us the button map)
    """
    print("\n--- Brute-Force Endpoint Read ---")
    print("  Trying each endpoint. Press buttons on the controller when prompted.\n")

    working_endpoints = []

    for ep_addr in CANDIDATE_ENDPOINTS:
        direction = "IN" if (ep_addr & 0x80) else "OUT"
        if direction != "IN":
            continue
        try:
            # 64-byte read with 500ms timeout
            data = dev.read(ep_addr, 64, timeout=500)
            print(f"  [HIT] Endpoint {ep_addr:#04x} returned {len(data)} bytes: {bytes(data).hex(' ')}")
            working_endpoints.append(ep_addr)
        except ValueError:
            # pyusb raises ValueError (not USBError) when the endpoint address is not
            # listed in the device's descriptor. This device has only one endpoint (0x81),
            # so all others will hit this. Just skip them silently.
            print(f"  [SKIP]   Endpoint {ep_addr:#04x} — not in device descriptor")
        except usb.core.USBError as e:
            if "timeout" in str(e).lower():
                # Timeout means the endpoint exists but no data arrived (device idle)
                print(f"  [TIMEOUT] Endpoint {ep_addr:#04x} — exists but no data (try pressing a button)")
                working_endpoints.append(ep_addr)
            elif "pipe" in str(e).lower() or "stall" in str(e).lower():
                print(f"  [STALL]  Endpoint {ep_addr:#04x} — endpoint exists but stalled (CLEAR_FEATURE needed?)")
            else:
                print(f"  [MISS]   Endpoint {ep_addr:#04x} — {e}")

    return working_endpoints


def live_monitor(dev, endpoints: list) -> None:
    """
    Continuously reads from confirmed working endpoints and prints hex data.
    Press buttons on the controller to see what bytes change.

    HOW TO READ THE OUTPUT:
    - Each line shows the raw bytes from one USB packet
    - Press one button at a time and note which byte/bit changes
    - E.g., if byte[0] goes from 0x00 to 0x01, bit 0 of byte 0 = that button
    - The lever/handle likely shows up as multiple bits or a separate byte

    Press Ctrl+C to stop.
    """
    if not endpoints:
        print("\n[!] No working endpoints found — cannot monitor. Check USB connection and try again.")
        return

    print(f"\n--- Live Button Monitor (endpoints: {[hex(e) for e in endpoints]}) ---")
    print("  Press buttons on the controller. Ctrl+C to stop.\n")

    last_data: dict = {}
    read_ep = endpoints[0]  # Use first working endpoint

    try:
        while True:
            try:
                raw = bytes(dev.read(read_ep, 64, timeout=100))
                if raw != last_data.get(read_ep):
                    last_data[read_ep] = raw
                    hex_str   = raw.hex(" ")
                    bits_str  = " ".join(f"{b:08b}" for b in raw)
                    print(f"  [{time.monotonic():.3f}]  hex={hex_str}")
                    print(f"            bits={bits_str}\n")
            except usb.core.USBError as e:
                if "timeout" not in str(e).lower():
                    print(f"  [read error] {e}")
    except KeyboardInterrupt:
        print("\n  Stopped.")


def main():
    print("=" * 60)
    print(" pachislot probe_device.py")
    print(f" Target: VID={VENDOR_ID:#06x}  PID={PRODUCT_ID:#06x}")
    print("=" * 60)

    # --- Step 1: Find the device ---
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("\n[ERROR] Device not found. Make sure it is plugged in.")
        print("  Run: lsusb | grep 0a7b")
        sys.exit(1)
    print(f"\n[OK] Found device: {dev}")

    # --- Step 2: Dump raw Device Descriptor ---
    raw_dev_desc = read_raw_descriptor(dev, USB_DT_DEVICE, length=18)
    if raw_dev_desc:
        hexdump(raw_dev_desc, "Raw Device Descriptor (18 bytes)")
    else:
        print("[!] Could not read Device Descriptor via control transfer (unusual)")

    # --- Step 3: Dump raw Configuration Descriptor (the problematic one) ---
    # First pass: ask for just 9 bytes to get wTotalLength
    raw_cfg_short = read_raw_descriptor(dev, USB_DT_CONFIG, length=9)
    if len(raw_cfg_short) >= 4:
        wTotalLength = struct.unpack_from("<H", raw_cfg_short, 2)[0]
        print(f"\n[INFO] Configuration Descriptor wTotalLength = {wTotalLength}")
        # Second pass: ask for the full length
        raw_cfg = read_raw_descriptor(dev, USB_DT_CONFIG, length=wTotalLength + 16)
    else:
        # Fallback: ask for a generous amount
        raw_cfg = read_raw_descriptor(dev, USB_DT_CONFIG, length=255)

    if raw_cfg:
        hexdump(raw_cfg, f"Raw Configuration Descriptor ({len(raw_cfg)} bytes received)")
        parse_config_descriptor(raw_cfg)
    else:
        print("[!] Could not read Configuration Descriptor — device may be in a bad state")

    # --- Step 4: Try to enumerate ---
    try_enumerate(dev)

    # --- Step 5: Try to claim interface 0 ---
    claimed = try_claim_interface(dev, 0)

    # --- Step 6: Brute-force endpoints ---
    working_eps = brute_force_read(dev)

    # --- Step 7: Live monitor ---
    if working_eps:
        print(f"\n[INFO] Found {len(working_eps)} working endpoint(s): {[hex(e) for e in working_eps]}")
        live_monitor(dev, working_eps)
    else:
        print("\n[INFO] No data endpoints found yet.")
        print("  Possible causes:")
        print("  1. Device needs a specific initialization command first (vendor control transfer)")
        print("  2. Endpoint addresses differ from the standard range we tried")
        print("  3. Linux also rejected the malformed descriptor — check: dmesg | grep -i usb | tail -20")
        print("\n  Try running: sudo dmesg | grep -i '0a7b\\|d001\\|pachislot\\|invalid'")

    usb.util.dispose_resources(dev)


if __name__ == "__main__":
    main()
