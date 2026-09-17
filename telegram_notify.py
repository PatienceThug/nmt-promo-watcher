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


def protect_chat_id(chat_id: str):
    """Protect the numeric chat id before storing it in the public repo state file."""
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
    r = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        return ""

    for update in reversed(data.get("result", [])):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            return str(chat["id"])
    return ""


def send_telegram(text: str, chat_id: str):
    if not BOT_TOKEN or not chat_id:
        print("[TG] Telegram destination unavailable; notification skipped")
        return False

    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
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
    state["sent_issue_numbers"] = state["sent_issue_numbers"][-1000:]
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_promo_issues():
    if not REPO or not GITHUB_TOKEN:
        raise RuntimeError("GitHub repository/token missing")

    r = requests.get(
        f"https://api.github.com/repos/{REPO}/issues",
        headers=github_headers(),
        params={
            "state": "all",
            "per_page": 50,
            "sort": "created",
            "direction": "desc",
        },
        timeout=20,
    )
    r.raise_for_status()
    issues = []
    for item in r.json():
        if "pull_request" in item:
            continue
        title = item.get("title") or ""
        if title.startswith("🚨 NMT PROMO:"):
            issues.append(item)
    return issues


def extract_source_lines(body: str):
    lines = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith("http://") or line.startswith("https://"):
            lines.append(line)
        elif "http://" in line or "https://" in line:
            for part in line.split():
                if part.startswith(("http://", "https://")):
                    lines.append(part.rstrip(")].,>"))
        if len(lines) >= 3:
            break
    return lines


def promo_message(issue):
    title = issue.get("title") or ""
    code = title.split(":", 1)[1].strip() if ":" in title else title
    issue_url = issue.get("html_url") or ""
    sources = extract_source_lines(issue.get("body") or "")

    parts = [
        "🚨 NMT PROMO KODU YAKALANDI",
        "",
        f"KOD: {code}",
        "",
        "Mümkün olduğunca hızlı dene; kullanım limiti olabilir.",
    ]
    if sources:
        parts += ["", "Kaynak:", *sources]
    if issue_url:
        parts += ["", f"GitHub kaydı: {issue_url}"]
    return "\n".join(parts)


def main():
    if not BOT_TOKEN:
        print("[TG] TELEGRAM_BOT_TOKEN not configured yet")
        return

    state = load_state()
    chat_id = CHAT_ID or recover_chat_id(state.get("chat_id_enc", ""))

    if not chat_id:
        chat_id = detect_chat_id_from_updates()
        if chat_id:
            state["chat_id_enc"] = protect_chat_id(chat_id)
            save_state(state)
            print("[TG] Telegram private chat discovered and protected in state")

    if not chat_id:
        print("[TG] No Telegram chat found. Send /start to the bot and wait for the next run.")
        return

    issues = get_promo_issues()
    sent = set(int(x) for x in state.get("sent_issue_numbers", []))

    if not state.get("initialized"):
        sent.update(int(issue["number"]) for issue in issues)
        send_telegram(
            "✅ NMT Promo Watcher aktif.\n\n"
            "Yeni NMT promo kodu yakalandığında bu sohbetten bildirim göndereceğim. "
            "GitHub alarmı da yedek olarak açık.",
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
        if send_telegram(promo_message(issue), chat_id):
            sent.add(int(issue["number"]))
            delivered += 1

    if unseen:
        state["sent_issue_numbers"] = sorted(sent)
        save_state(state)

    print(f"[TG] delivered={delivered}, unseen={len(unseen)}")


if __name__ == "__main__":
    main()
