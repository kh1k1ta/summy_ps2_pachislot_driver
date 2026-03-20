#!/usr/bin/env python3
"""
pachislot_receiver.py — Windows側: ネットワークからボタンイベントを受信し仮想Xbox360パッドに注入

SETUP:
  pip install vgamepad

USAGE:
  python pachislot_receiver.py [--host 192.168.1.100] [--port 5555]

ARCHITECTURE:
  [Linux PC: pachislot_sender.py]
      → TCP:5555
      → [このスクリプト]
      → ViGEmBus
      → 仮想Xbox360コントローラー
      → ゲームに入力が届く
"""

import argparse
import json
import logging
import socket
import struct
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

try:
    import vgamepad as vg
except ImportError:
    log.error("vgamepad が見つかりません。インストールしてください: pip install vgamepad")
    sys.exit(1)


# ─── ボタンマッピング: 論理名 → ViGEmBus Xbox360 ボタン ──────────────────────

BUTTON_MAP = {
    "A":     vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "B":     vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    "X":     vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "Y":     vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    "LB":    vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    "RB":    vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "BACK":  vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
    "START": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
}

# ─── プロトコル（sender側と同じ）────────────────────────────────────────────

HEADER_FMT = "!I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    """指定バイト数を確実に受信"""
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("接続が閉じられました")
        buf += chunk
    return buf


def recv_message(sock: socket.socket) -> dict:
    """長さプレフィックス付きJSONメッセージを受信"""
    header = recv_exact(sock, HEADER_SIZE)
    (length,) = struct.unpack(HEADER_FMT, header)
    if length > 65536:
        raise ValueError(f"メッセージが大きすぎます: {length} bytes")
    payload = recv_exact(sock, length)
    return json.loads(payload.decode("utf-8"))


def run_receiver(host: str, port: int):
    # 仮想ゲームパッド作成
    try:
        gamepad = vg.VX360Gamepad()
        log.info("仮想Xbox360パッド作成完了 (ViGEmBus)")
    except Exception as ex:
        log.error("ViGEmBus 仮想パッド作成失敗: %s", ex)
        log.error("vgamepad が正しくインストールされているか確認してください。")
        sys.exit(1)

    while True:
        log.info("Linux側へ接続中... %s:%d", host, port)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((host, port))
            sock.settimeout(None)
            log.info("接続成功!")
        except (ConnectionRefusedError, socket.timeout, OSError) as ex:
            log.warning("接続失敗: %s — 3秒後にリトライ", ex)
            time.sleep(3)
            continue

        try:
            # helloメッセージ受信
            hello = recv_message(sock)
            if hello.get("type") == "hello":
                log.info("デバイス接続確認: %s", hello.get("device", "unknown"))

            # ボタンイベント受信ループ
            while True:
                msg = recv_message(sock)

                if msg.get("type") != "button":
                    continue

                btn_name = msg.get("button")
                pressed = msg.get("pressed", False)

                xusb_button = BUTTON_MAP.get(btn_name)
                if xusb_button is None:
                    log.warning("未知のボタン: %s", btn_name)
                    continue

                if pressed:
                    gamepad.press_button(button=xusb_button)
                else:
                    gamepad.release_button(button=xusb_button)
                gamepad.update()

                log.debug("%s %s", btn_name, "PRESSED" if pressed else "released")

        except (ConnectionError, ConnectionResetError, json.JSONDecodeError) as ex:
            log.warning("接続エラー: %s — 再接続します", ex)
        except KeyboardInterrupt:
            log.info("停止。")
            break
        finally:
            try:
                sock.close()
            except Exception:
                pass

            # 全ボタンリリース（安全のため）
            try:
                gamepad.reset()
                gamepad.update()
            except Exception:
                pass

        time.sleep(2)

    log.info("Done.")


def main():
    parser = argparse.ArgumentParser(description="pachislot ネットワーク受信 → ViGEmBus仮想パッド")
    parser.add_argument("--host", required=True,
                        help="Linux PCのIPアドレス (例: 192.168.1.100)")
    parser.add_argument("--port", "-p", type=int, default=5555,
                        help="接続ポート (デフォルト: 5555)")
    args = parser.parse_args()

    run_receiver(args.host, args.port)


if __name__ == "__main__":
    main()
