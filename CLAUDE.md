Hi I want to make a windows driver to provide slotmachine-like controller as keyboard(or joypad?).
I have "summy モウジュウオウ controller" originally for PS2 pachislot game. it can connect to PC via USB, but there is not proper driver to use (once it connected, it will cause code 43 error).
It wont recognized as HID device nor joypad too. 
how can i get started? is it needed to analize USB? give an overview to achive this goal pleaseeee



# Getting Your パチスロコントローラー Working on Windows

This is a fun project! Let me give you a clear overview of what's involved.

## Why It Doesn't Work Now

The "Code 43" error means Windows loaded a driver, sent a request the device didn't answer correctly (or Windows doesn't understand the device's USB descriptors), and gave up. PS2-era controllers like this often use vendor-specific USB protocols rather than standard HID — so Windows has no idea what to do with it.

## The Overall Plan

There are roughly three phases to this project:

**Phase 1: USB Analysis (the critical first step)**

Before writing any driver, you need to understand what the device actually *says* on the USB bus. You'll want to capture its descriptors (vendor ID, product ID, endpoint configuration, interface class) and see what data it sends when you press buttons. Tools for this:

- **USBDeview** or **USB Device Tree Viewer** — free tools that can show you the device's VID/PID and descriptor info even when it's in a Code 43 state. Start here.
- **Wireshark + USBPcap** — lets you capture raw USB traffic. You plug the controller in, press buttons, and watch what packets come through. This is the most important step of the whole project.
- **If you have a working PS2 + game**: you could also observe the protocol in its native environment using a USB protocol analyzer, but Wireshark on PC is usually enough.

**Phase 2: Choose Your Driver Approach**

Once you know the protocol, you have a few options ranked from easiest to hardest:

1. **libusb / WinUSB + userspace mapping** (recommended starting point) — Install a generic WinUSB driver using **Zadig** (replaces the failing driver with one that just gives you raw USB access). Then write a small userspace program in C/Python that reads the device via libusb, decodes button presses, and injects keyboard/gamepad inputs using something like **vJoy** (virtual joystick) or **SendInput** (virtual keyboard). No kernel driver needed.

2. **ViGEmBus** — A virtual gamepad driver framework. Your userspace app reads the USB data and feeds it into a virtual Xbox 360 or DualShock 4 controller. Games see a normal gamepad.

3. **Custom WDM/KMDF kernel driver** — A real Windows kernel-mode driver. This is the "proper" way but is extremely complex (kernel debugging, driver signing, BSOD risk). Only worth it if the above approaches fail.

**Phase 3: Button Mapping & Polish**

Once data flows, you map physical inputs (levers, buttons on the controller — presumably stuff like BET, MAX BET, START, the lever/handle) to keyboard keys or gamepad buttons. Ideally you'd make a small config GUI.

## Recommended Concrete Steps

The path I'd suggest:

1. **Install USB Device Tree Viewer** → plug in the controller → note the VID, PID, and descriptor details
2. **Install Wireshark + USBPcap** → capture traffic → press every button and pull the lever → identify the data patterns
3. **Install Zadig** → assign WinUSB driver to the device (this replaces the Code 43 driver)
4. **Write a Python script** using `pyusb` (wraps libusb) to read the endpoints and print raw data as you press buttons
5. **Add vJoy or keyboard output** → map decoded buttons to virtual inputs
6. Done — your パチスロコントローラー works as a gamepad/keyboard

## Do You Need to "Analyze USB"?

Yes, absolutely — this is unavoidable and also the most interesting part. Without knowing the device's protocol, you're flying blind. But the good news is that for a simple controller (probably just a few buttons + a lever), the protocol is likely very simple — maybe just a few bytes of interrupt transfer data where each bit represents a button state.

Want me to help you with any specific phase? For instance, I could put together a Python script template for reading the device once you have the VID/PID, or help you interpret USB descriptor dumps.


here is a report of that USB controller;

I tried to capture some packets in wireshark, but there were no expected packets  appeared (only insert/eject interruption packet captured)



# Analysis of Your USB Descriptor Dump

This is very revealing — and it explains a lot about why things are difficult.

## What the Dump Tells Us

The key problem is right here:

**"Invalid Configuration Descriptor"** and **"CONFIGURATION_DESCRIPTOR_VALIDATION_FAILURE"**

The device sends back a Device Descriptor just fine (VID `0x0A7B`, PID `0xD001`), but when Windows asks for the Configuration Descriptor, the device returns something Windows considers malformed. This means Windows **aborts enumeration before it ever fully sets up the device** — that's why:

- You get Code 43
- Wireshark only shows attach/detach events (no actual data transfers)
- No endpoints are opened ("0 pipes to data endpoints")
- String descriptors can't be read

The device is essentially stuck at "I exist" but never reaches "I'm ready to communicate."

## Why This Happens

This was a PS2 peripheral. The PS2's USB implementation was notoriously loose — many PS2 USB accessories have slightly non-compliant descriptors that the PS2 accepted but modern Windows rejects. The device probably has a subtly malformed Configuration Descriptor (wrong total length, bad endpoint descriptors, etc.).

## Your Options Going Forward

**Option A: Patch the descriptor in a custom driver (hard but proper)**

Write a KMDF USB filter driver that intercepts the Configuration Descriptor response from the device, fixes the malformed bytes, and passes the corrected version up to Windows. This lets normal enumeration proceed. This is real kernel driver work — doable but steep learning curve.

**Option B: Use Linux as your analysis/operating system (recommended first step)**

Linux's USB stack is much more forgiving than Windows. There's a good chance the device will enumerate successfully on Linux even with the bad descriptor. This lets you:

1. Read the full (raw) descriptors including the broken Configuration Descriptor with `lsusb -v`
2. See what endpoints actually exist
3. Capture real data traffic with `usbmon` / Wireshark
4. Write a userspace driver with `libusb` to test button inputs

Even if your end goal is Windows, do the analysis on Linux first. A live USB of Ubuntu is enough.

**Option C: Intercept and analyze at the raw USB level**

Use a hardware USB protocol analyzer (like a cheap logic analyzer with USB decoding, e.g., a Saleae clone) to capture exactly what Configuration Descriptor bytes the device sends. Then you'll know precisely what's "wrong" and how to fix it.

## Suggested Next Steps

Here's what I'd do in your position:

1. **Boot a Linux live USB** → plug in the controller → run `lsusb -v -d 0a7b:d001` → see if Linux enumerates it and what the raw descriptors look like
2. **If Linux works**: use `usbmon` + Wireshark on Linux to capture button press data, then write a Python/libusb script to decode the protocol
3. **Bring it back to Windows**: once you know the protocol and what the descriptor *should* look like, you can either write a KMDF filter driver that patches the descriptor, or use a modified WinUSB/libusb approach with a custom INF that forces the device through enumeration
4. **Map inputs**: use vJoy or ViGEmBus to expose it as a gamepad

The Linux step is really the key breakthrough — it'll likely bypass the descriptor validation issue and let you actually see what this device does. Want me to help you put together the exact Linux commands or a libusb script for probing the device?
============================================================
 pachislot probe_device.py
 Target: VID=0x0a7b  PID=0xd001
============================================================

[OK] Found device: DEVICE ID 0a7b:d001 on Bus 001 Address 003 =================
 bLength                :   0x12 (18 bytes)
 bDescriptorType        :    0x1 Device
 bcdUSB                 :  0x100 USB 1.0
 bDeviceClass           :    0x0 Specified at interface
 bDeviceSubClass        :    0x0
 bDeviceProtocol        :    0x0
 bMaxPacketSize0        :    0x8 (8 bytes)
 idVendor               : 0x0a7b
 idProduct              : 0xd001
 bcdDevice              :  0x100 Device 1.0
 iManufacturer          :    0x1 Sammy CORPORATION
 iProduct               :    0x2 Sammy Pach-slot
 iSerialNumber          :    0x0 
 bNumConfigurations     :    0x1
  CONFIGURATION 1: 400 mA ==================================
   bLength              :    0x9 (9 bytes)
   bDescriptorType      :    0x2 Configuration
   wTotalLength         :   0x22 (34 bytes)
   bNumInterfaces       :    0x1
   bConfigurationValue  :    0x1
   iConfiguration       :    0x0 
   bmAttributes         :   0x80 Bus Powered
   bMaxPower            :   0xc8 (400 mA)
    INTERFACE 0: Vendor Specific ===========================
     bLength            :    0x9 (9 bytes)
     bDescriptorType    :    0x4 Interface
     bInterfaceNumber   :    0x0
     bAlternateSetting  :    0x0
     bNumEndpoints      :    0x1
     bInterfaceClass    :   0xff Vendor Specific
     bInterfaceSubClass :    0x0
     bInterfaceProtocol :    0x0
     iInterface         :    0x0 
      ENDPOINT 0x81: Interrupt IN ==========================
       bLength          :    0x7 (7 bytes)
       bDescriptorType  :    0x5 Endpoint
       bEndpointAddress :   0x81 IN
       bmAttributes     :    0x3 Interrupt
       wMaxPacketSize   :    0x8 (8 bytes)
       bInterval        :    0xa

=== Raw Device Descriptor (18 bytes) ===
  0000:  12 01 00 01 00 00 00 08 7B 0A 01 D0 00 01 01 02   ........{.......
  0010:  00 01                                             ..

[INFO] Configuration Descriptor wTotalLength = 34

=== Raw Configuration Descriptor (34 bytes received) ===
  0000:  09 02 22 00 01 01 00 80 C8 09 04 00 00 01 FF 00   ..".............
  0010:  00 00 07 05 81 03 08 00 0A 04 03 09 04 24 03 53   .............$.S
  0020:  00 61                                             .a

--- Manual Configuration Descriptor Walk ---
  [CONFIGURATION] wTotalLength=34  bNumInterfaces=1  bConfigValue=1
  [INTERFACE]     bInterfaceNumber=0  bNumEndpoints=1  class=0xff  subclass=0x00  protocol=0x00
  [ENDPOINT]      address=0x81 (IN)  type=Interrupt  wMaxPacketSize=8  bInterval=10
  [DESC type=0x03]  length=4
  [DESC type=0x03]  length=36

--- Attempting Device Enumeration ---
  [OK] set_configuration(1) succeeded via pyusb
  [OK] Claimed interface 0

--- Brute-Force Endpoint Read ---
  Trying each endpoint. Press buttons on the controller when prompted.

  [HIT] Endpoint 0x81 returned 4 bytes: 00 00 00 00
============================================================
 pachislot probe_device.py
 Target: VID=0x0a7b  PID=0xd001
============================================================

[OK] Found device: DEVICE ID 0a7b:d001 on Bus 001 Address 003 =================
 bLength                :   0x12 (18 bytes)
 bDescriptorType        :    0x1 Device
 bcdUSB                 :  0x100 USB 1.0
 bDeviceClass           :    0x0 Specified at interface
 bDeviceSubClass        :    0x0
 bDeviceProtocol        :    0x0
 bMaxPacketSize0        :    0x8 (8 bytes)
 idVendor               : 0x0a7b
 idProduct              : 0xd001
 bcdDevice              :  0x100 Device 1.0
 iManufacturer          :    0x1 Sammy CORPORATION
 iProduct               :    0x2 Sammy Pach-slot
 iSerialNumber          :    0x0 
 bNumConfigurations     :    0x1
  CONFIGURATION 1: 400 mA ==================================
   bLength              :    0x9 (9 bytes)
   bDescriptorType      :    0x2 Configuration
   wTotalLength         :   0x22 (34 bytes)
   bNumInterfaces       :    0x1
   bConfigurationValue  :    0x1
   iConfiguration       :    0x0 
   bmAttributes         :   0x80 Bus Powered
   bMaxPower            :   0xc8 (400 mA)
    INTERFACE 0: Vendor Specific ===========================
     bLength            :    0x9 (9 bytes)
     bDescriptorType    :    0x4 Interface
     bInterfaceNumber   :    0x0
     bAlternateSetting  :    0x0
     bNumEndpoints      :    0x1
     bInterfaceClass    :   0xff Vendor Specific
     bInterfaceSubClass :    0x0
     bInterfaceProtocol :    0x0
     iInterface         :    0x0 
      ENDPOINT 0x81: Interrupt IN ==========================
       bLength          :    0x7 (7 bytes)
       bDescriptorType  :    0x5 Endpoint
       bEndpointAddress :   0x81 IN
       bmAttributes     :    0x3 Interrupt
       wMaxPacketSize   :    0x8 (8 bytes)
       bInterval        :    0xa

=== Raw Device Descriptor (18 bytes) ===
  0000:  12 01 00 01 00 00 00 08 7B 0A 01 D0 00 01 01 02   ........{.......
  0010:  00 01                                             ..

[INFO] Configuration Descriptor wTotalLength = 34

=== Raw Configuration Descriptor (34 bytes received) ===
  0000:  09 02 22 00 01 01 00 80 C8 09 04 00 00 01 FF 00   ..".............
  0010:  00 00 07 05 81 03 08 00 0A 04 03 09 04 24 03 53   .............$.S
  0020:  00 61                                             .a

--- Manual Configuration Descriptor Walk ---
  [CONFIGURATION] wTotalLength=34  bNumInterfaces=1  bConfigValue=1
  [INTERFACE]     bInterfaceNumber=0  bNumEndpoints=1  class=0xff  subclass=0x00  protocol=0x00
  [ENDPOINT]      address=0x81 (IN)  type=Interrupt  wMaxPacketSize=8  bInterval=10
  [DESC type=0x03]  length=4
  [DESC type=0x03]  length=36

--- Attempting Device Enumeration ---
  [OK] set_configuration(1) succeeded via pyusb
  [OK] Claimed interface 0

--- Brute-Force Endpoint Read ---
  Trying each endpoint. Press buttons on the controller when prompted.

  [HIT] Endpoint 0x81 returned 4 bytes: 00 00 00 00
Traceback (most recent call last):
  File "/home/kanon/pachislot/venv/lib/python3.13/site-packages/usb/core.py", line 241, in get_interface_and_endpoint
    return self._ep_info[endpoint_address]
           ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^
KeyError: 130

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/home/kanon/pachislot/probe_device.py", line 336, in <module>
    main()
    ~~~~^^
  File "/home/kanon/pachislot/probe_device.py", line 318, in main
    working_eps = brute_force_read(dev)
  File "/home/kanon/pachislot/probe_device.py", line 216, in brute_force_read
    data = dev.read(ep_addr, 64, timeout=500)
  File "/home/kanon/pachislot/venv/lib/python3.13/site-packages/usb/core.py", line 1035, in read
    intf, ep = self._ctx.setup_request(self, endpoint)
               ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^
  File "/home/kanon/pachislot/venv/lib/python3.13/site-packages/usb/core.py", line 113, in wrapper
    return f(self, *args, **kwargs)
  File "/home/kanon/pachislot/venv/lib/python3.13/site-packages/usb/core.py", line 233, in setup_request
    intf, ep = self.get_interface_and_endpoint(device, endpoint_address)
               ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/kanon/pachislot/venv/lib/python3.13/site-packages/usb/core.py", line 113, in wrapper
    return f(self, *args, **kwargs)
  File "/home/kanon/pachislot/venv/lib/python3.13/site-packages/usb/core.py", line 251, in get_interface_and_endpoint
    raise ValueError('Invalid endpoint address ' + hex(endpoint_address))
ValueError: Invalid endpoint address 0x82
ONEBET hex=00 00 01 00
MAXBET hex=00 00 04 00
LEVER hex=00 00 80 00
FIRST_STOP hex=00 00 10 00
SECOND_STOP hex=00 00 20 00
THIRD_STOP hex=00 00 40 00
COIN hex=00 00 08 00
START hex=00 20 00 00


# MEMORY.md
---
name: pachislot controller project
description: Writing a userspace USB driver for summy モウジュウオウ PS2 pachislot controller (VID 0x0A7B, PID 0xD001) to work as a virtual gamepad on Linux and Windows
type: project
---

Project: make the summy モウジュウオウ pachislot controller (originally for PS2) work as a virtual gamepad on PC.

**Why:** Device causes Code 43 on Windows — its Configuration Descriptor is malformed (extra string descriptor fragments embedded inside it). Windows rejects it; Linux is lenient and enumerates it fine.

**Solution approach:** Userspace driver (no kernel driver needed)
- Linux: pyusb reads USB + evdev/uinput virtual gamepad
- Windows: Zadig installs WinUSB (bypasses descriptor validation) + pyusb + vgamepad (ViGEmBus → virtual Xbox 360 controller)

**Device facts confirmed via probe_device.py:**
- VID=0x0A7B, PID=0xD001, USB 1.0, Manufacturer="Sammy CORPORATION", Product="Sammy Pach-slot"
- One endpoint: 0x81, Interrupt IN, wMaxPacketSize=8, bInterval=10ms
- Packets are 4 bytes. Rest state = `00 00 00 00`
- Button map (confirmed by pressing each button):
  - byte[2] bit 0x01 = ONE_BET → A
  - byte[2] bit 0x04 = MAX_BET → B
  - byte[2] bit 0x80 = LEVER   → LB
  - byte[2] bit 0x10 = STOP1   → X
  - byte[2] bit 0x20 = STOP2   → Y
  - byte[2] bit 0x40 = STOP3   → RB
  - byte[2] bit 0x08 = COIN    → BACK
  - byte[1] bit 0x20 = START   → START

**Files:** ~/pachislot/
  - pachislot_driver.py  — cross-platform driver (Linux working, Windows untested as of 2026-03-21)
  - probe_device.py      — USB analysis/debug tool
  - setup.sh             — Linux one-time setup

**Current status (2026-03-21):** Linux driver confirmed working. User rebooted to Windows to test Windows path (Zadig + vgamepad).

**Why:** User wants to use the controller on Windows for pachislot games/emulators.

**How to apply:** If user returns asking about this project, the Linux side is done. Focus on Windows: Zadig → `pip install pyusb libusb-package vgamepad` → `python pachislot_driver.py`. If they hit issues, likely culprits are Zadig not replacing the driver correctly, or ViGEmBus not loading.
