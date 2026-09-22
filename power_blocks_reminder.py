"""Telegram-only Power Blocks reminder.

This script never signs in to NMT, reads account cookies, or performs gameplay.
It only sends a Telegram reminder with the public Power Blocks link.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib import request

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
STATE_PATH = Path("telegram_state.json")
POWER_BLOCKS_URL = "https://nmt.gg/power-blocks"


def telegram(method, payload=None):
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    last = None
    for attempt in range(3):
        try:
            req = request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
            with request.urlopen(req, timeout=25) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(f"Telegram API failed: {method}")
            return result.get("result")
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Telegram {method} failed after retries: {last}")


def recover_chat_id(value):
    if not BOT_TOKEN or not value:
        return ""
    try:
        encrypted = base64.urlsafe_b64decode(value.encode())
        key = hashlib.sha256((BOT_TOKEN + "::nmt-chat-id").encode()).digest()
        raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
        chat_id = raw.decode()
        return chat_id if chat_id.lstrip("-").isdigit() else ""
    except Exception:
        return ""


def load_saved_chat_id():
    if not STATE_PATH.exists():
        return ""
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return recover_chat_id(state.get("chat_id_enc", ""))


def detect_private_chat():
    updates = telegram("getUpdates") or []
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            return str(chat["id"])
    return ""


def send(chat_id, test=False):
    if test:
        text = (
            "✅ Power Blocks hatırlatıcısı aktif.\n\n"
            "Sistem NMT hesabına giriş yapmıyor, cookie/API kullanmıyor ve senin yerine "
            "Power Blocks yerleştirmiyor. Yalnızca Telegram hatırlatması gönderiyor.\n\n"
            "Hedef aralık: 10 dakika. GitHub Actions zamanlaması bazen birkaç dakika gecikebilir."
        )
    else:
        text = (
            "⚡ POWER BLOCKS — KONTROL ZAMANI\n\n"
            "Yaklaşık 10 dakika geçti. Yeni turu kontrol edip yerleştirmeyi kendin yapabilirsin."
        )

    telegram("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "⚡ Power Blocks Aç", "url": POWER_BLOCKS_URL}
            ]]
        },
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

    if args.test:
        bot = telegram("getMe") or {}
        username = bot.get("username")
        if username:
            print(f"[POWER] Connected bot: @{username}")

    chat_id = CHAT_ID or load_saved_chat_id() or detect_private_chat()
    if not chat_id:
        raise RuntimeError("Telegram private chat could not be found")

    send(chat_id, test=args.test)
    print("[POWER] Reminder delivered")


if __name__ == "__main__":
    main()
