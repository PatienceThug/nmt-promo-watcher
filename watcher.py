import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

REPO = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
STATE_PATH = Path("seen_codes.json")
ALERT_THREAD = int(os.environ.get("ALERT_THREAD", "1"))
NOW = datetime.now(timezone.utc)

TELEGRAM_CHANNELS = {
    "NMT Official": "nmt_official",
    "Kripto Master": "kriptomastr",
    "Cryptanchan": "cryptanchan",
    "Vse v TON": "ktotovdele",
    "Cryptofuga": "cryptofuga",
}

NMT_PAGES = {
    "NMT News EN": "https://nmt.gg/en/news",
    "NMT Academy EN": "https://nmt.gg/en/academy",
    "NMT Club EN": "https://nmt.gg/en/club",
    "NMT Explorer EN": "https://nmt.gg/en/explorer",
    "NMT News TR": "https://nmt.gg/tr/news",
    "NMT Academy TR": "https://nmt.gg/tr/academy",
    "NMT Club TR": "https://nmt.gg/tr/club",
    "NMT Explorer TR": "https://nmt.gg/tr/explorer",
}

MIRRORS = {
    "Telemetr Kripto Master": "https://telemetr.io/en/channels/1715417045-kriptomastr",
    "Telemetr Vse v TON": "https://telemetr.io/uk/channels/2996997406-ktotovdele/posts",
    "Telemetr Cryptofuga": "https://telemetr.io/en/channels/1285380086-cryptofuga/posts",
}

SEARCH_QUERIES = [
    '"nmt.gg" "promo code"',
    '"nmt.gg" promocode',
    '"nmt.gg" "promo kod"',
    '"nmt.gg" промокод',
    '"nmt.gg" "gift code"',
]

YOUTUBE_QUERIES = [
    "NMT.GG promo code",
    "NMT GG promocode",
    "NMT.GG promo kod",
    "NMT GG промокод",
    "NMT.GG gift code",
]

KEYWORDS = re.compile(
    r"(promo\s*code|promocode|promo\s*kod\w*|promokod\w*|"
    r"промокод\w*|промо\s*код\w*|промо-код\w*|"
    r"bonus\s*code|gift\s*code|hediye\s*kod\w*|free\s*case|"
    r"coupon\s*code|voucher\s*code)",
    re.I,
)

DIRECT_PATTERNS = [
    re.compile(
        r"(?:promo\s*code|promocode|promo\s*kod\w*|promokod\w*|"
        r"промокод\w*|промо\s*код\w*|промо-код\w*|"
        r"bonus\s*code|gift\s*code|hediye\s*kod\w*)"
        r"\s*[:=/#\-–—]*\s*[`\"'“”‘’]*([A-Z0-9][A-Z0-9_\-]{4,31})",
        re.I,
    ),
    re.compile(
        r"([A-Z0-9][A-Z0-9_\-]{4,31})[`\"'“”‘’]*\s*"
        r"(?:promo\s*code|promocode|promokod|промокод|hediye\s*kodu)",
        re.I,
    ),
]

BAD = {
    "PROMOCODE", "PROMOKOD", "PROMO-CODE", "WELCOME", "TELEGRAM",
    "YOUTUBE", "BONUSCODE", "GIFTCODE", "POWERBLOCKS", "DISCORD",
    "INSTAGRAM", "TWITTER", "NMT.GG", "HTTPS", "STARTAPP",
}

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
        "Chrome/124.0 Mobile Safari/537.36"
    )
}


def session():
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(UA)
    return s


HTTP = session()


def clean_code(code: str):
    code = code.upper().strip("`'\"“”‘’.,:;()[]{}<>")
    if not 5 <= len(code) <= 32:
        return None
    if code in BAD:
        return None
    if code.startswith(("HTTP", "WWW")):
        return None
    if not any(c.isalpha() for c in code):
        return None
    return code


def extract_codes(text: str):
    compact = " ".join((text or "").split())
    found = {}

    for pattern in DIRECT_PATTERNS:
        for match in pattern.finditer(compact):
            code = clean_code(match.group(1))
            if code:
                start = max(0, match.start() - 140)
                end = min(len(compact), match.end() + 220)
                found[code] = compact[start:end]

    for keyword in KEYWORDS.finditer(compact):
        window = compact[max(0, keyword.start() - 180): keyword.end() + 320]
        for raw in re.findall(r"\b[A-Z0-9][A-Z0-9_\-]{4,31}\b", window.upper()):
            code = clean_code(raw)
            if not code or code in found:
                continue
            if any(c.isdigit() for c in code) or len(code) >= 8:
                found[code] = window

    return found


def fetch_html(url: str):
    r = HTTP.get(url, timeout=20)
    r.raise_for_status()
    return r.text


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def event_id(code, source, url, context):
    raw = f"{code}|{source}|{url}|{context[:400]}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


def make_event(code, source, url, context, published_at=None):
    return {
        "id": event_id(code, source, url, context),
        "code": code,
        "source": source,
        "url": url,
        "context": context[:700],
        "published_at": published_at,
    }


def scan_telegram(name, channel):
    url = f"https://t.me/s/{channel}"
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    events = []
    posts = soup.select("div.tgme_widget_message")

    if not posts:
        text = soup.get_text(" ", strip=True)
        for code, context in extract_codes(text).items():
            events.append(make_event(code, name, url, context))
        return events

    for post in posts:
        body = post.select_one(".tgme_widget_message_text")
        if not body:
            continue
        text = body.get_text(" ", strip=True)
        codes = extract_codes(text)
        if not codes:
            continue

        date_link = post.select_one("a.tgme_widget_message_date")
        post_url = date_link.get("href") if date_link else url
        time_el = post.select_one("time")
        published = time_el.get("datetime") if time_el else None

        for code, context in codes.items():
            events.append(make_event(code, name, post_url, context, published))
    return events


def scan_generic(name, url):
    html = fetch_html(url)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return [
        make_event(code, name, url, context)
        for code, context in extract_codes(text).items()
    ]


def scan_ddg():
    events = []
    for query in SEARCH_QUERIES:
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        try:
            html = fetch_html(url)
            soup = BeautifulSoup(html, "html.parser")
            for result in soup.select(".result"):
                a = result.select_one(".result__a")
                snippet = result.select_one(".result__snippet")
                text = " ".join(
                    x.get_text(" ", strip=True)
                    for x in (a, snippet)
                    if x
                )
                if "nmt" not in text.lower():
                    continue
                target = a.get("href") if a else url
                for code, context in extract_codes(text).items():
                    events.append(make_event(code, f"Web search: {query}", target, context))
        except Exception as exc:
            print(f"[WARN] DDG {query}: {exc}")
    return events


def scan_youtube():
    events = []
    for query in YOUTUBE_QUERIES:
        try:
            proc = subprocess.run(
                [
                    "yt-dlp",
                    "--skip-download",
                    "--dump-json",
                    "--playlist-end", "12",
                    f"ytsearchdate12:{query}",
                ],
                capture_output=True,
                text=True,
                timeout=150,
            )
            if proc.returncode not in (0, 1):
                print(f"[WARN] YouTube {query}: return {proc.returncode}")
            for line in proc.stdout.splitlines():
                try:
                    item = json.loads(line)
                except Exception:
                    continue

                title = item.get("title") or ""
                desc = item.get("description") or ""
                url = item.get("webpage_url") or item.get("original_url") or ""
                published = None
                upload_date = item.get("upload_date")
                if upload_date and len(upload_date) == 8:
                    try:
                        published = datetime.strptime(upload_date, "%Y%m%d").replace(
                            tzinfo=timezone.utc
                        ).isoformat()
                    except Exception:
                        pass

                text = title + "\n" + desc
                for code, context in extract_codes(text).items():
                    events.append(
                        make_event(
                            code,
                            f"YouTube: {title[:100]}",
                            url,
                            context,
                            published,
                        )
                    )
        except Exception as exc:
            print(f"[WARN] YouTube {query}: {exc}")
    return events


def load_state():
    if not STATE_PATH.exists():
        return {"version": 2, "seen_event_ids": [], "code_last_alert": {}}, True
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 2:
            data.setdefault("seen_event_ids", [])
            data.setdefault("code_last_alert", {})
            return data, False

        old_codes = data.get("codes", []) if isinstance(data, dict) else []
        upgraded = {
            "version": 2,
            "seen_event_ids": [],
            "code_last_alert": {
                str(code).upper(): "2000-01-01T00:00:00+00:00"
                for code in old_codes
            },
        }
        return upgraded, False
    except Exception:
        return {"version": 2, "seen_event_ids": [], "code_last_alert": {}}, True


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["seen_event_ids"] = state["seen_event_ids"][-5000:]
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def github_post(path, payload):
    if not REPO or not GITHUB_TOKEN:
        print("[WARN] GitHub API skipped: token/repo missing")
        return None
    r = HTTP.post(
        f"https://api.github.com/repos/{REPO}{path}",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=payload,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def create_alert(code, events):
    owner = REPO.split("/", 1)[0] if "/" in REPO else None
    source_lines = []
    for event in events[:4]:
        when = event.get("published_at") or "timestamp unavailable"
        source_lines.append(
            f"- **{event['source']}** — {when}\n  {event['url']}"
        )

    body = (
        "## 🚨 Yeni NMT promo kodu\n\n"
        f"# `{code}`\n\n"
        + "\n".join(source_lines)
        + "\n\n"
        f"**Yakalanma zamanı (UTC):** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "Kod sınırlı kullanımlı olabilir. Geçerliliği NMT.GG üzerinde hemen kontrol et."
    )

    issue_payload = {
        "title": f"🚨 NMT PROMO: {code}",
        "body": body,
    }
    if owner:
        issue_payload["assignees"] = [owner]

    issue = github_post("/issues", issue_payload)
    issue_url = issue.get("html_url") if issue else ""

    comment = (
        f"🚨 **NMT PROMO ALARMI:** `{code}`\n\n"
        f"{events[0]['source']}: {events[0]['url']}\n\n"
        + (f"Arşiv: {issue_url}" if issue_url else "")
    )
    try:
        github_post(f"/issues/{ALERT_THREAD}/comments", {"body": comment})
    except Exception as exc:
        print(f"[WARN] alert-thread comment failed: {exc}")

    print(f"[ALERT] {code}")


def is_recent(event, hours=12):
    published = parse_iso(event.get("published_at"))
    if not published:
        return False
    age = NOW - published
    return age.total_seconds() >= -3600 and age.total_seconds() <= hours * 3600


def should_realert(last_alert, hours=12):
    if not last_alert:
        return True
    dt = parse_iso(last_alert)
    if not dt:
        return True
    return (NOW - dt).total_seconds() >= hours * 3600


def scan(mode):
    events = []
    success = 0
    failures = 0

    for name, channel in TELEGRAM_CHANNELS.items():
        try:
            events.extend(scan_telegram(name, channel))
            success += 1
        except Exception as exc:
            failures += 1
            print(f"[WARN] Telegram {name}: {exc}")

    for name, url in NMT_PAGES.items():
        try:
            events.extend(scan_generic(name, url))
            success += 1
        except Exception as exc:
            failures += 1
            print(f"[WARN] {name}: {exc}")

    if mode == "deep":
        for name, url in MIRRORS.items():
            try:
                events.extend(scan_generic(name, url))
                success += 1
            except Exception as exc:
                failures += 1
                print(f"[WARN] {name}: {exc}")

        events.extend(scan_ddg())
        events.extend(scan_youtube())

    print(f"[HEALTH] successful sources={success}, failures={failures}, events={len(events)}")
    if success == 0:
        raise RuntimeError("All primary sources failed")
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fast", "deep"), default="fast")
    args = parser.parse_args()

    events = scan(args.mode)
    unique = {event["id"]: event for event in events}
    events = list(unique.values())

    state, first_run = load_state()
    seen = set(state.get("seen_event_ids", []))
    last_alert = state.get("code_last_alert", {})

    if first_run:
        recent_by_code = defaultdict(list)
        for event in events:
            if is_recent(event, hours=12):
                recent_by_code[event["code"]].append(event)

        for code, code_events in sorted(recent_by_code.items()):
            create_alert(code, code_events)
            last_alert[code] = datetime.now(timezone.utc).isoformat()

        seen.update(event["id"] for event in events)
        state["seen_event_ids"] = list(seen)
        state["code_last_alert"] = last_alert
        save_state(state)
        print(f"[BASELINE] {len(events)} events stored; {len(recent_by_code)} recent code alert(s)")
        return

    new_events = [event for event in events if event["id"] not in seen]
    grouped = defaultdict(list)
    for event in new_events:
        grouped[event["code"]].append(event)

    alerted = 0
    for code, code_events in sorted(grouped.items()):
        if should_realert(last_alert.get(code), hours=12):
            create_alert(code, code_events)
            last_alert[code] = datetime.now(timezone.utc).isoformat()
            alerted += 1
        else:
            print(f"[DUPLICATE] {code} seen on a new source within 12h")

    if new_events:
        seen.update(event["id"] for event in new_events)
        state["seen_event_ids"] = list(seen)
        state["code_last_alert"] = last_alert
        save_state(state)

    print(f"[DONE] new_events={len(new_events)} alerted_codes={alerted}")


if __name__ == "__main__":
    main()
