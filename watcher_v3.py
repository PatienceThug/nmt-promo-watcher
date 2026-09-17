import argparse
import hashlib
import json
import os
import re
import subprocess
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
ALERT_THREAD = int(os.environ.get("ALERT_THREAD", "1"))
STATE_PATH = Path("seen_codes.json")
NOW = datetime.now(timezone.utc)

TELEGRAM_CHANNELS = {
    "NMT Official": "nmt_official",
    "Kripto Master": "kriptomastr",
    "Cryptanchan": "cryptanchan",
    "Vse v TON": "ktotovdele",
    "Cryptofuga": "cryptofuga",
}

# These are redundancy sources. They are especially useful if t.me changes markup
# or a public Telegram page temporarily stops rendering for GitHub-hosted runners.
MIRRORS = {
    "TG.ME NMT Official": "https://tg.me/nmt_official",
    "TG.ME Kripto Master": "https://tg.me/kriptomastr",
    "TG.ME Cryptanchan": "https://tg.me/cryptanchan",
    "TG.ME Vse v TON": "https://tg.me/ktotovdele",
    "Telemetr NMT Official": "https://telemetr.me/content/nmt_official",
    "Telemetr Kripto Master": "https://telemetr.io/en/channels/1715417045-kriptomastr",
}

# NMT currently returns 403 to many cloud runners. Keep these as opportunistic
# sources, but do not treat their failure as a watcher outage.
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

SEARCH_QUERIES = [
    '"nmt.gg" "promo code"',
    '"nmt.gg" promocode',
    '"nmt.gg" "promo kod"',
    '"nmt.gg" "hediye kodu"',
    '"nmt.gg" промокод',
    '"NMT" "promo code" "t.me"',
]

YOUTUBE_QUERIES = [
    "NMT.GG promo code",
    "NMT GG promocode",
    "NMT.GG promo kod",
    "NMT GG промокод",
]

KEYWORDS = re.compile(
    r"(promo\s*code|promocode|promo\s*kod\w*|promokod\w*|"
    r"промокод\w*|промо\s*код\w*|промо-код\w*|"
    r"bonus\s*code|gift\s*code|hediye\s*kod\w*|hediye\s*code|"
    r"free\s*case|ücretsiz\s*case|coupon\s*code|voucher\s*code)",
    re.I,
)

DIRECT_PATTERNS = [
    re.compile(
        r"(?:promo\s*code|promocode|promo\s*kod\w*|promokod\w*|"
        r"промокод\w*|промо\s*код\w*|промо-код\w*|"
        r"bonus\s*code|gift\s*code|hediye\s*kod\w*|hediye\s*code)"
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
    "INSTAGRAM", "TWITTER", "NMT.GG", "HTTPS", "STARTAPP", "MARKETPLACE",
    "COLLECTION", "ACTIVATION", "ACTIVATIONS", "REGISTER", "REFERRAL",
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
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(UA)
    return s


HTTP = session()


def fetch_html(url: str, timeout=16):
    try:
        r = HTTP.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text
    except requests.HTTPError:
        # NMT blocks many cloud-runner IPs with 403. Jina Reader is a free,
        # read-only fallback that returns the public page as text.
        if url.startswith("https://nmt.gg/"):
            fallback = "https://r.jina.ai/http://" + url.removeprefix("https://")
            r = HTTP.get(
                fallback,
                headers={"X-Cache-Tolerance": "0", "X-Retain-Images": "none"},
                timeout=max(timeout, 30),
            )
            r.raise_for_status()
            print(f"[FALLBACK] NMT via reader: {url}")
            return r.text
        raise


def clean_code(code: str):
    code = code.upper().strip("`'\"“”‘’.,:;()[]{}<>")
    if not 5 <= len(code) <= 32:
        return None
    if code in BAD or code.startswith(("HTTP", "WWW")):
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
                start = max(0, match.start() - 160)
                end = min(len(compact), match.end() + 280)
                found[code] = compact[start:end]

    # Secondary recovery path: catches codes formatted oddly but only near a promo keyword.
    for keyword in KEYWORDS.finditer(compact):
        window = compact[max(0, keyword.start() - 180): keyword.end() + 340]
        for raw in re.findall(r"\b[A-Z0-9][A-Z0-9_\-]{4,31}\b", window.upper()):
            code = clean_code(raw)
            if not code or code in found:
                continue
            if any(c.isdigit() for c in code) or len(code) >= 6:
                found[code] = window

    return found


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def is_recent(event, hours=48):
    dt = parse_iso(event.get("published_at"))
    if not dt:
        return False
    age = (NOW - dt).total_seconds()
    return -3600 <= age <= hours * 3600


def event_id(code, source, url, context):
    raw = f"{code}|{source}|{url}|{context[:500]}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


def make_event(code, source, url, context, published_at=None, kind="mirror"):
    return {
        "id": event_id(code, source, url, context),
        "code": code,
        "source": source,
        "url": url,
        "context": context[:900],
        "published_at": published_at,
        "kind": kind,
    }


def scan_telegram(name, channel):
    url = f"https://t.me/s/{channel}"
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    events = []
    posts = soup.select("div.tgme_widget_message")

    if not posts:
        raise RuntimeError("Telegram page rendered without message blocks")

    for post in posts:
        body = post.select_one(".tgme_widget_message_text")
        if not body:
            continue
        text = body.get_text(" ", strip=True)
        codes = extract_codes(text)
        if not codes:
            continue

        link = post.select_one("a.tgme_widget_message_date")
        post_url = link.get("href") if link else url
        time_el = post.select_one("time")
        published = time_el.get("datetime") if time_el else None
        for code, context in codes.items():
            events.append(make_event(code, name, post_url, context, published, "telegram"))
    return events


def scan_generic(name, url, kind="mirror"):
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    text = soup.get_text(" ", strip=True)
    return [make_event(code, name, url, context, None, kind) for code, context in extract_codes(text).items()]


def scan_ddg():
    events = []
    for query in SEARCH_QUERIES:
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        try:
            soup = BeautifulSoup(fetch_html(url, timeout=18), "html.parser")
            for result in soup.select(".result"):
                a = result.select_one(".result__a")
                snippet = result.select_one(".result__snippet")
                text = " ".join(x.get_text(" ", strip=True) for x in (a, snippet) if x)
                if "nmt" not in text.lower():
                    continue
                target = a.get("href") if a else url
                for code, context in extract_codes(text).items():
                    events.append(make_event(code, f"Web search: {query}", target, context, None, "search"))
        except Exception as exc:
            print(f"[WARN] DDG {query}: {exc}")
    return events


def scan_youtube():
    events = []
    for query in YOUTUBE_QUERIES:
        try:
            proc = subprocess.run(
                [
                    "yt-dlp", "--skip-download", "--dump-json",
                    "--playlist-end", "8", f"ytsearchdate8:{query}",
                ],
                capture_output=True,
                text=True,
                timeout=95,
            )
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
                        published = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
                    except Exception:
                        pass
                for code, context in extract_codes(title + "\n" + desc).items():
                    events.append(make_event(code, f"YouTube: {title[:90]}", url, context, published, "youtube"))
        except Exception as exc:
            print(f"[WARN] YouTube {query}: {exc}")
    return events


def load_state():
    default = {
        "version": 3,
        "seen_event_ids": [],
        "code_last_alert": {},
        "health": {"primary_failure_streak": 0, "last_health_alert": None},
    }
    if not STATE_PATH.exists():
        return default, True
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default, True
        data["version"] = 3
        data.setdefault("seen_event_ids", [])
        data.setdefault("code_last_alert", {})
        data.setdefault("health", {"primary_failure_streak": 0, "last_health_alert": None})
        data["health"].setdefault("primary_failure_streak", 0)
        data["health"].setdefault("last_health_alert", None)
        return data, False
    except Exception:
        return default, True


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["seen_event_ids"] = state.get("seen_event_ids", [])[-7000:]
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


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


def activation_hint(context):
    patterns = [
        r"(?:ilk|first|only)\s*(\d{1,5})\s*(?:kişi|user|users|activation|activations)",
        r"(\d{1,5})\s*(?:aktivasyon|activation|activations|активац\w*)",
    ]
    for p in patterns:
        m = re.search(p, context or "", re.I)
        if m:
            return m.group(1)
    return None


def confidence(events):
    kinds = {e.get("kind") for e in events}
    recent_direct = any(e.get("kind") == "telegram" and is_recent(e, 48) for e in events)
    recent_yt = any(e.get("kind") == "youtube" and is_recent(e, 72) for e in events)
    independent = len({e.get("source") for e in events})
    if recent_direct or independent >= 2:
        return "Yüksek"
    if recent_yt or "mirror" in kinds:
        return "Orta"
    return "Düşük"


def create_promo_alert(code, events):
    owner = REPO.split("/", 1)[0] if "/" in REPO else None
    sources = []
    for event in events[:5]:
        when = event.get("published_at") or "timestamp unavailable"
        sources.append(f"- **{event['source']}** — {when}\n  {event['url']}")

    hint = next((activation_hint(e.get("context", "")) for e in events if activation_hint(e.get("context", ""))), None)
    body = (
        "## 🚨 Yeni NMT promo kodu\n\n"
        f"# `{code}`\n\n"
        f"**Güven:** {confidence(events)}\n"
        + (f"**İlanda görülen limit:** {hint} aktivasyon/kullanıcı\n" if hint else "")
        + "\n".join(sources)
        + "\n\n"
        f"**Yakalanma zamanı (UTC):** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "Kod sınırlı olabilir. Geçerliliği NMT.GG üzerinde mümkün olduğunca hızlı kontrol et."
    )
    payload = {"title": f"🚨 NMT PROMO: {code}", "body": body}
    if owner:
        payload["assignees"] = [owner]
    issue = github_post("/issues", payload)
    issue_url = issue.get("html_url") if issue else ""
    try:
        github_post(
            f"/issues/{ALERT_THREAD}/comments",
            {"body": f"🚨 **NMT PROMO:** `{code}`\n\n{events[0]['source']}: {events[0]['url']}\n\n{issue_url}"},
        )
    except Exception as exc:
        print(f"[WARN] alert thread comment failed: {exc}")
    print(f"[ALERT] {code} confidence={confidence(events)}")


def create_health_alert(primary_ok, errors):
    details = "\n".join(f"- {x}" for x in errors[:8]) or "Primary source count too low."
    github_post(
        "/issues",
        {
            "title": "⚠️ NMT WATCHER HEALTH: primary sources degraded",
            "body": (
                "## Kaynak sağlık uyarısı\n\n"
                f"Çalışan ana Telegram kaynağı: **{primary_ok}/{len(TELEGRAM_CHANNELS)}**\n\n"
                f"{details}\n\n"
                "Watcher çalışmaya devam ediyor; mirror/web/YouTube yedekleri devrede."
            ),
        },
    )


def should_realert(last_alert, hours=12):
    dt = parse_iso(last_alert)
    if not dt:
        return True
    return (NOW - dt).total_seconds() >= hours * 3600


def eligible_group(events, code_seen_before):
    # Timestamped direct Telegram / fresh YouTube is strong enough by itself.
    if any(e.get("kind") == "telegram" and is_recent(e, 48) for e in events):
        return True
    if any(e.get("kind") == "youtube" and is_recent(e, 72) for e in events):
        return True

    # For timestamp-less mirrors/search results, demand corroboration unless the code
    # has never been seen before. This prevents a new mirror from resurrecting stale codes.
    independent = len({e.get("source") for e in events})
    if independent >= 2 and not code_seen_before:
        return True
    return False


def scan(mode):
    events = []
    primary_ok = 0
    errors = []

    for name, channel in TELEGRAM_CHANNELS.items():
        try:
            events.extend(scan_telegram(name, channel))
            primary_ok += 1
        except Exception as exc:
            msg = f"Telegram {name}: {exc}"
            errors.append(msg)
            print(f"[WARN] {msg}")

    # Opportunistic official pages. Their failure does not count as a primary outage.
    for name, url in NMT_PAGES.items():
        try:
            events.extend(scan_generic(name, url, "official-web"))
        except Exception as exc:
            print(f"[WARN] {name}: {exc}")

    if mode == "deep":
        for name, url in MIRRORS.items():
            try:
                events.extend(scan_generic(name, url, "mirror"))
            except Exception as exc:
                print(f"[WARN] {name}: {exc}")
        events.extend(scan_ddg())
        events.extend(scan_youtube())

    print(f"[HEALTH] primary_ok={primary_ok}/{len(TELEGRAM_CHANNELS)} events={len(events)} mode={mode}")
    if primary_ok == 0 and not events:
        raise RuntimeError("All primary sources failed and no fallback events were collected")
    return events, primary_ok, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fast", "deep"), default="fast")
    args = parser.parse_args()

    events, primary_ok, errors = scan(args.mode)
    unique = {e["id"]: e for e in events}
    events = list(unique.values())

    state, first_run = load_state()
    seen = set(state.get("seen_event_ids", []))
    last_alert = state.get("code_last_alert", {})
    health = state.get("health", {})

    # Health watchdog: alert only after three consecutive degraded runs, max once/hour.
    if primary_ok < 2:
        health["primary_failure_streak"] = int(health.get("primary_failure_streak", 0)) + 1
    else:
        health["primary_failure_streak"] = 0

    last_health = parse_iso(health.get("last_health_alert"))
    health_cooldown_ok = not last_health or (NOW - last_health).total_seconds() >= 3600
    if health["primary_failure_streak"] >= 3 and health_cooldown_ok:
        create_health_alert(primary_ok, errors)
        health["last_health_alert"] = NOW.isoformat()

    state["health"] = health

    if first_run:
        # Safety baseline: do not dump old mirror/search codes on a fresh install.
        recent = defaultdict(list)
        for e in events:
            if e.get("kind") in ("telegram", "youtube") and is_recent(e, 24):
                recent[e["code"]].append(e)
        for code, grouped in recent.items():
            create_promo_alert(code, grouped)
            last_alert[code] = NOW.isoformat()
        seen.update(e["id"] for e in events)
        state["seen_event_ids"] = list(seen)
        state["code_last_alert"] = last_alert
        save_state(state)
        print(f"[BASELINE] events={len(events)} recent_alerts={len(recent)}")
        return

    new_events = [e for e in events if e["id"] not in seen]
    grouped = defaultdict(list)
    for e in new_events:
        grouped[e["code"]].append(e)

    alerted = 0
    for code, group in sorted(grouped.items()):
        code_seen_before = code in last_alert
        if not eligible_group(group, code_seen_before):
            print(f"[HOLD] {code} only stale/uncorroborated fallback evidence")
            continue
        if should_realert(last_alert.get(code), 12):
            create_promo_alert(code, group)
            last_alert[code] = NOW.isoformat()
            alerted += 1
        else:
            print(f"[DUPLICATE] {code} suppressed within 12h")

    seen.update(e["id"] for e in new_events)
    state["seen_event_ids"] = list(seen)
    state["code_last_alert"] = last_alert
    save_state(state)
    print(f"[DONE] new_events={len(new_events)} alerted_codes={alerted}")


if __name__ == "__main__":
    main()
