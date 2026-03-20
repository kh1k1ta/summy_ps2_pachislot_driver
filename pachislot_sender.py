#!/usr/bin/env python3
"""
pachislot_sender.py — Linux側: evdevイベントをネットワーク経由でWindows側に送信する

USAGE:
  sudo python3 pachislot_sender.py [--device /dev/input/event4] [--port 5555]

REQUIRES:
  sudo apt install python3-evdev

ARCHITECTURE:
  [pachislot_driver.py が作った仮想ゲームパッド]
      → /dev/input/eventX
      → [このスクリプト]
      → TCP:5555
      → [Windows側 pachislot_receiver.py]
      → ViGEmBus 仮想Xbox360パッド
"""

import argparse
import json
import logging
import os
import socket
import struct
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

try:
    import evdev
    from evdev import ecodes as ec, InputDevice
except ImportError:
    log.error("evdev が見つかりません。インストールしてください: sudo apt install python3-evdev")
    sys.exit(1)


# ─── ボタンマッピング（pachislot_driver.py と一致させる）─────────────────────
# evdevコード → 論理名
EVDEV_TO_LOGICAL = {
    ec.BTN_A:      "A",       # ONE_BET
    ec.BTN_B:      "B",       # MAX_BET
    ec.BTN_X:      "X",       # STOP 1
    ec.BTN_Y:      "Y",       # STOP 2
    ec.BTN_TL:     "LB",      # LEVER
    ec.BTN_TR:     "RB",      # STOP 3
    ec.BTN_SELECT: "BACK",    # COIN
    ec.BTN_START:  "START",   # START
}

# ─── プロトコル ───────────────────────────────────────────────────────────────
# シンプルな形式:
#   4バイト (big-endian uint32) = JSONペイロードの長さ
#   Nバイト = JSON文字列 {"button": "A", "pressed": true}

HEADER_FMT = "!I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


def send_message(sock: socket.socket, msg: dict) -> bool:
    """JSONメッセージを長さプレフィックス付きで送信"""
    try:
        payload = json.dumps(msg).encode("utf-8")
        header = struct.pack(HEADER_FMT, len(payload))
        sock.sendall(header + payload)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError) as ex:
        log.warning("送信失敗: %s", ex)
        return False


def find_pachislot_device() -> str:
    """pachislot_driver.py が作った仮想ゲームパッドを自動検出"""
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
            if "summy" in dev.name.lower() or "pachislot" in dev.name.lower():
                return path
        except Exception:
            continue
    return ""


def run_sender(device_path: str, host: str, port: int):
    # デバイスを開く
    if not device_path:
        device_path = find_pachislot_device()
        if not device_path:
            log.error("pachislotデバイスが見つかりません。--device で指定するか、pachislot_driver.py が動いているか確認してください。")
            sys.exit(1)
        log.info("自動検出: %s", device_path)

    dev = InputDevice(device_path)
    log.info("デバイスオープン: %s (%s)", dev.name, dev.path)

    # 監視対象のボタンコード
    watched_codes = set(EVDEV_TO_LOGICAL.keys())

    # TCP サーバーとして待ち受け
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)
    log.info("Windows側の接続を待機中... %s:%d", host, port)

    while True:
        conn, addr = server.accept()
        log.info("接続: %s:%d", addr[0], addr[1])

        # 接続確認メッセージ
        send_message(conn, {"type": "hello", "device": dev.name})

        try:
            dev.grab()  # 他のプロセスからの入力を排他
            log.info("デバイスをgrab — イベント送信開始")

            for event in dev.read_loop():
                if event.type != ec.EV_KEY:
                    continue
                if event.code not in watched_codes:
                    continue

                btn_name = EVDEV_TO_LOGICAL[event.code]
                # event.value: 1=press, 0=release, 2=repeat(無視)
                if event.value == 2:
                    continue

                msg = {
                    "type": "button",
                    "button": btn_name,
                    "pressed": event.value == 1,
                }
                log.debug("%s %s", btn_name, "PRESSED" if event.value == 1 else "released")

                if not send_message(conn, msg):
                    log.warning("接続切断 — 再接続を待機")
                    break

        except KeyboardInterrupt:
            log.info("停止。")
            break
        except OSError as ex:
            log.warning("デバイスエラー: %s — 再接続待機", ex)
        finally:
            try:
                dev.ungrab()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

        log.info("Windows側の再接続を待機中...")

    server.close()


def main():
    parser = argparse.ArgumentParser(description="pachislot evdev → ネットワーク送信")
    parser.add_argument("--device", "-d", default="",
                        help="evdev デバイスパス (例: /dev/input/event4)。省略で自動検出。")
    parser.add_argument("--host", default="0.0.0.0",
                        help="バインドアドレス (デフォルト: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=5555,
                        help="待受ポート (デフォルト: 5555)")
    args = parser.parse_args()

    run_sender(args.device, args.host, args.port)


if __name__ == "__main__":
    main()
