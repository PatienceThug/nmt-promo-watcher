import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
STATE_PATH = Path("telegram_state.json")


def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_bot_identity():
    if not BOT_TOKEN:
        return ""
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        return ""
    username = (data.get("result") or {}).get("username") or ""
    if username:
        print(f"[TG] Connected bot: @{username}")
    return username


def protect_chat_id(chat_id: str):
    if not BOT_TOKEN or not chat_id:
        return ""
    key = hashlib.sha256((BOT_TOKEN + "::nmt-chat-id").encode()).digest()
    raw = chat_id.encode()
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return base64.urlsafe_b64encode(encrypted).decode()


def recover_chat_id(value: str):
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


def detect_chat_id_from_updates():
    if not BOT_TOKEN:
        return ""
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        return ""
    updates = data.get("result", [])
    print(f"[TG] getUpdates returned {len(updates)} update(s)")
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            return str(chat["id"])
    return ""


def send_telegram(text: str, chat_id: str, copy_code: str = ""):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if copy_code and 1 <= len(copy_code) <= 256:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "📋 Kodu kopyala", "copy_text": {"text": copy_code}}
            ]]
        }
        # Telegram entity offsets use UTF-16, including the leading emoji.
        marker = "KOD: " + copy_code
        start = text.find(marker)
        if start >= 0:
            start += len("KOD: ")
            payload["entities"] = [{
                "type": "code",
                "offset": len(text[:start].encode("utf-16-le")) // 2,
                "length": len(copy_code.encode("utf-16-le")) // 2,
            }]
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return True


def load_state():
    if not STATE_PATH.exists():
        return {"initialized": False, "sent_issue_numbers": [], "chat_id_enc": ""}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        data.setdefault("initialized", False)
        data.setdefault("sent_issue_numbers", [])
        data.setdefault("chat_id_enc", "")
        return data
    except Exception:
        return {"initialized": False, "sent_issue_numbers": [], "chat_id_enc": ""}


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def get_alert_issues():
    if not REPO or not GITHUB_TOKEN:
        raise RuntimeError("GitHub repository/token missing")
    alerts = []
    page = 1
    while True:
        r = requests.get(
            f"https://api.github.com/repos/{REPO}/issues",
            headers=github_headers(),
            params={"state": "all", "per_page": 100, "sort": "created",
                    "direction": "desc", "page": page},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json()
        for item in items:
            if "pull_request" in item:
                continue
            title = item.get("title") or ""
            if title.startswith(("🚨 NMT PROMO:", "⚠️ NMT WATCHER HEALTH:")):
                alerts.append(item)
        if len(items) < 100:
            return alerts
        page += 1


def extract_urls(body: str):
    urls = []
    for raw in (body or "").splitlines():
        for part in raw.strip().split():
            if part.startswith(("http://", "https://")):
                url = part.rstrip(")].,>")
                if url not in urls:
                    urls.append(url)
        if len(urls) >= 3:
            break
    return urls


def extract_field(body: str, marker: str):
    for raw in (body or "").splitlines():
        line = raw.strip().replace("**", "")
        if line.lower().startswith(marker.lower()):
            return line.split(":", 1)[1].strip() if ":" in line else ""
    return ""


def alert_message(issue):
    title = issue.get("title") or ""
    body = issue.get("body") or ""
    issue_url = issue.get("html_url") or ""

    if title.startswith("🚨 NMT PROMO:"):
        code = title.split(":", 1)[1].strip()
        confidence = extract_field(body, "Güven")
        limit = extract_field(body, "İlanda görülen limit")
        urls = extract_urls(body)
        parts = ["🚨 NMT PROMO KODU YAKALANDI", "", f"KOD: {code}"]
        if confidence:
            parts.append(f"Güven: {confidence}")
        if limit:
            parts.append(f"Limit: {limit}")
        parts += ["", "Hızlı dene; kullanım sayısı sınırlı olabilir."]
        if urls:
            parts += ["", "Kaynak:", *urls]
        if issue_url:
            parts += ["", f"GitHub kaydı: {issue_url}"]
        return "\n".join(parts)

    if title.startswith("⚠️ NMT WATCHER HEALTH: Workflow"):
        return f"⚠️ NMT taraması başarısız oldu.\n\nDetay: {issue_url}"

    return (
        "⚠️ NMT WATCHER KAYNAK UYARISI\n\n"
        "Ana kaynakların bir kısmı art arda erişilemedi. Sistem yedek kaynaklarla çalışmaya devam ediyor.\n\n"
        f"Detay: {issue_url}"
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN eksik; Telegram bildirimleri gönderilemiyor")

    get_bot_identity()
    state = load_state()
    chat_id = CHAT_ID or recover_chat_id(state.get("chat_id_enc", ""))

    if not chat_id:
        chat_id = detect_chat_id_from_updates()
        if chat_id:
            state["chat_id_enc"] = protect_chat_id(chat_id)
            save_state(state)
            print("[TG] Telegram private chat discovered and protected in state")

    if not chat_id:
        raise RuntimeError("Telegram sohbeti bulunamadı; TELEGRAM_CHAT_ID ayarını kontrol edin")

    issues = get_alert_issues()
    sent = set(int(x) for x in state.get("sent_issue_numbers", []))

    if not state.get("initialized"):
        sent.update(int(issue["number"]) for issue in issues)
        send_telegram(
            "✅ NMT Promo Watcher aktif.\n\n"
            "Yeni promo kodları ve önemli kaynak sağlık sorunları bu sohbetten bildirilecek. "
            "GitHub kaydı yedek olarak açık.",
            chat_id,
        )
        state["initialized"] = True
        state["sent_issue_numbers"] = sorted(sent)
        save_state(state)
        print("[TG] Telegram initialized and test message sent")
        return

    unseen = [issue for issue in reversed(issues) if int(issue["number"]) not in sent]
    delivered = 0
    for issue in unseen:
        title = issue.get("title") or ""
        copy_code = title.split(":", 1)[1].strip() if title.startswith("🚨 NMT PROMO:") else ""
        if send_telegram(alert_message(issue), chat_id, copy_code=copy_code):
            sent.add(int(issue["number"]))
            delivered += 1
            state["sent_issue_numbers"] = sorted(sent)
            save_state(state)

    if unseen:
        state["sent_issue_numbers"] = sorted(sent)
        save_state(state)

    print(f"[TG] delivered={delivered}, unseen={len(unseen)}")


if __name__ == "__main__":
    main()
