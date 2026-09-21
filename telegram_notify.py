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
    except Exception as exc:
        raise RuntimeError("Telegram state unreadable; refusing to replay alerts") from exc


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _health_issue_is_current(item):
    """Only deliver live health incidents, never replay old/closed failures."""
    if (item.get("state") or "").lower() != "open":
        return False

    created_at = item.get("created_at") or ""
    if not created_at:
        return True

    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return True

    return (datetime.now(timezone.utc) - created).total_seconds() <= 6 * 3600


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
            if title.startswith("🚨 NMT PROMO:"):
                alerts.append(item)
            elif title.startswith("⚠️ NMT WATCHER HEALTH:") and _health_issue_is_current(item):
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

    if title.startswith("⚠️ NMT WATCHER HEALTH: Workflow delivery"):
        return f"⚠️ NMT bildirim/durum kaydı sorunu.\n\n{body.splitlines()[0] if body else 'Tarama sonucu ayrıca kontrol edilmeli.'}\n\nDetay: {issue_url}"

    if title.startswith("⚠️ NMT WATCHER HEALTH: Workflow"):
        return f"⚠️ NMT taraması başarısız oldu.\n\nDetay: {issue_url}"

    return (
        "⚠️ NMT WATCHER KAYNAK UYARISI\n\n"
        "Ana kaynakların bir kısmı art arda erişilemedi. Sistem yedek kaynaklarla çalışmaya devam ediyor.\n\n"
        f"Detay: {issue_url}"
    )


def promo_key(issue):
    title = issue.get("title") or ""
    if title.startswith("🚨 NMT PROMO:"):
        return title.split(":", 1)[1].strip().strip("`\"'").upper()
    return ""


def notify_issues(state, issues, chat_id):
    sent = set(int(x) for x in state.get("sent_issue_numbers", []))
    codes = set(state.get("sent_codes", []))
    # Migrate the existing delivery history before considering new issue IDs.
    codes.update(promo_key(i) for i in issues if int(i["number"]) in sent and promo_key(i))
    now = datetime.now(timezone.utc)
    health_at = state.get("last_health_sent_at")
    delivered = suppressed = 0
    for issue in sorted(issues, key=lambda i: int(i["number"])):
        number = int(issue["number"])
        if number in sent:
            continue
        code = promo_key(issue)
        duplicate = bool(code and code in codes)
        if not code and health_at:
            last = datetime.fromisoformat(health_at)
            duplicate = (now - last).total_seconds() < 24 * 3600
        if duplicate:
            suppressed += 1
        else:
            raw_code = (issue.get("title") or "").split(":", 1)[1].strip() if code else ""
            send_telegram(alert_message(issue), chat_id, copy_code=raw_code)
            delivered += 1
            if code:
                codes.add(code)
            else:
                health_at = now.isoformat()
        sent.add(number)
        state["sent_issue_numbers"] = sorted(sent)
        state["sent_codes"] = sorted(codes)
        if health_at:
            state["last_health_sent_at"] = health_at
        save_state(state)
        # Save receipts immediately, before a later source/state failure can lose them.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            from persist_state import persist_files
            persist_files({"telegram_state.json": state})
    state["sent_codes"] = sorted(codes)
    save_state(state)
    print(f"[TG] delivered={delivered}, suppressed={suppressed}")


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
        state["sent_codes"] = sorted({promo_key(i) for i in issues if promo_key(i)})
        save_state(state)
        print("[TG] Telegram initialized and test message sent")
        return

    notify_issues(state, issues, chat_id)


if __name__ == "__main__":
    main()
