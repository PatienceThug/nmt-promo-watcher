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
STATE_PATH = Path("social_state.json")
NOW = datetime.now(timezone.utc)

OFFICIAL_X_HANDLE = "nmt_off"
OFFICIAL_X_URL = f"https://x.com/{OFFICIAL_X_HANDLE}"
JINA_X_URL = f"https://r.jina.ai/http://twitter.com/{OFFICIAL_X_HANDLE}"

X_QUERIES_FAST = [
    'site:x.com/nmt_off "nmt.gg"',
    'site:x.com/nmt_off (promo OR promocode OR promokod OR промокод)',
]

X_QUERIES_DEEP = [
    'site:x.com "nmt.gg" "promo code"',
    'site:x.com "nmt.gg" promocode',
    'site:x.com "nmt.gg" "promo kod"',
    'site:x.com "nmt.gg" промокод',
    'site:x.com "#NMTGG" promo',
    'site:x.com "@nmt_off" "nmt.gg"',
    'site:twstalker.com "nmt.gg" promo',
]

YOUTUBE_QUERIES = [
    "NMT.GG",
    "NMTGG",
    "#NMTGG",
    "NMT.GG promo code",
    "NMT.GG promocode",
    "NMT.GG promo kod",
    "NMT GG промокод",
]

PROMO_WORDS = re.compile(
    r"(promo\s*code|promocode|promo\s*kod\w*|promokod\w*|"
    r"промокод\w*|промо\s*код\w*|промо-код\w*|"
    r"bonus\s*code|gift\s*code|hediye\s*kod\w*|voucher\s*code|coupon\s*code)",
    re.I,
)

DIRECT_PATTERNS = [
    re.compile(
        r"(?:promo\s*code|promocode|promo\s*kod\w*|promokod\w*|"
        r"промокод\w*|промо\s*код\w*|промо-код\w*|"
        r"bonus\s*code|gift\s*code|hediye\s*kod\w*|voucher\s*code|coupon\s*code)"
        r"\s*[:=/#\-–—]*\s*[`\"'“”‘’]*([A-Z0-9][A-Z0-9_\-]{4,31})",
        re.I,
    ),
    re.compile(
        r"([A-Z0-9][A-Z0-9_\-]{4,31})[`\"'“”‘’]*\s*"
        r"(?:promo\s*code|promocode|promokod|промокод|promo\s*kod\w*|hediye\s*kod\w*)",
        re.I,
    ),
]

BAD = {
    "YARARLANABILIRSINIZ", "YATIRIM", "YOKTUR", "VARLIKLAR", "TAVSIYESI", "TARAFINDA", "SISTEMIYLE", "SISTEMINDEN", "PLATFORMLAR", "PLATFORMDAKI", "MODELLERINIZI", "MODELLER", "KULLANARAK", "KRIPTOMASTER", "KRIPTO", "KAZANABILIR", "GARANTISI", "FIRSATLARDAN", "FARKLI", "ETMEYE", "EDIYORUZ", "EDIYOR", "DETAYLI", "ALINAN", "BLOCKS", "AVAILABLE", "EXPIRED", "REDEEM", "LIMITED",
    "PROMOCODE", "PROMOKOD", "PROMO-CODE", "NMTGG", "NMT.GG", "YOUTUBE",
    "TWITTER", "TELEGRAM", "DISCORD", "HTTPS", "GIVEAWAY", "ACTIVATION",
    "ACTIVATIONS", "REGISTER", "REFERRAL", "MARKETPLACE", "COLLECTION",
}

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
        "Chrome/124.0 Mobile Safari/537.36"
    )
}


def http_session():
    s = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(UA)
    return s


HTTP = http_session()


def clean_code(value):
    code = (value or "").upper().strip("`'\"“”‘’.,:;()[]{}<>")
    if not 5 <= len(code) <= 32:
        return None
    if code in BAD or code.startswith(("HTTP", "WWW")):
        return None
    if not any(ch.isalpha() for ch in code):
        return None
    return code


def extract_codes(text):
    # Only take candidates directly attached to a promo label. Never turn an
    # entire paragraph into uppercase candidates: prose is not code evidence.
    compact = " ".join((text or "").split())
    found = {}
    for pattern in DIRECT_PATTERNS:
        for match in pattern.finditer(compact):
            raw = match.group(1)
            begin, end = match.span(1)
            if begin and (compact[begin - 1].isalnum() or compact[begin - 1] in "_-"):
                continue
            if end < len(compact) and (compact[end].isalnum() or compact[end] in "_-"):
                continue
            code = clean_code(raw)
            if not code:
                continue
            # Letter-only codes need explicit original uppercase formatting.
            if not any(ch.isdigit() for ch in raw) and raw != raw.upper():
                continue
            found[code] = compact[max(0, match.start() - 160):match.end() + 280]
    return found


def event_id(code, source, url, context):
    raw = f"{code}|{source}|{url}|{context[:600]}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


def event(code, source, url, context, published_at=None, kind="social"):
    return {
        "id": event_id(code, source, url, context),
        "code": code,
        "source": source,
        "url": url,
        "context": context[:1100],
        "published_at": published_at,
        "kind": kind,
    }


def fetch_text(url, headers=None, timeout=22):
    r = HTTP.get(url, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.text


def scan_official_x():
    # Jina Reader is used because direct x.com HTML frequently requires login/JS.
    text = fetch_text(
        JINA_X_URL,
        headers={"X-Cache-Tolerance": "0", "X-Retain-Images": "none"},
        timeout=30,
    )
    if OFFICIAL_X_HANDLE.lower() not in text.lower() and "NMT" not in text:
        raise RuntimeError("official X reader returned unexpected content")

    events = []
    for code, context in extract_codes(text).items():
        events.append(event(code, "X Official @nmt_off", OFFICIAL_X_URL, context, None, "x-official"))
    return events


def ddg_results(query):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    soup = BeautifulSoup(fetch_text(url, timeout=20), "html.parser")
    rows = []
    for result in soup.select(".result"):
        a = result.select_one(".result__a")
        snip = result.select_one(".result__snippet")
        text = " ".join(x.get_text(" ", strip=True) for x in (a, snip) if x)
        target = a.get("href") if a else url
        rows.append((text, target))
    return rows


def scan_x_search(deep=False):
    events = []
    queries = list(X_QUERIES_FAST)
    if deep:
        queries += X_QUERIES_DEEP

    for query in queries:
        try:
            for text, target in ddg_results(query):
                low = text.lower()
                if "nmt" not in low and "nmt.gg" not in low:
                    continue
                for code, context in extract_codes(text).items():
                    events.append(event(code, f"X/Web search: {query}", target, context, None, "x-search"))
        except Exception as exc:
            print(f"[SOCIAL WARN] X search {query}: {exc}")
    return events


def upload_iso(item):
    value = item.get("timestamp")
    if value:
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except Exception:
            pass
    value = item.get("upload_date")
    if value and len(value) == 8:
        try:
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    return None


def scan_youtube_tags():
    events = []
    seen_video_ids = set()

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
            if proc.stderr:
                for line in proc.stderr.splitlines()[-3:]:
                    if "ERROR" in line.upper():
                        print(f"[SOCIAL WARN] YouTube {query}: {line[:220]}")

            for line in proc.stdout.splitlines():
                try:
                    item = json.loads(line)
                except Exception:
                    continue

                video_id = str(item.get("id") or "")
                if video_id and video_id in seen_video_ids:
                    continue
                if video_id:
                    seen_video_ids.add(video_id)

                title = item.get("title") or ""
                desc = item.get("description") or ""
                tags = item.get("tags") or []
                if not isinstance(tags, list):
                    tags = [str(tags)]
                channel = item.get("channel") or item.get("uploader") or ""
                url = item.get("webpage_url") or item.get("original_url") or ""

                tag_text = " ".join(str(x) for x in tags)
                haystack = "\n".join([title, desc, tag_text, channel])
                low = haystack.lower()
                if "nmt.gg" not in low and "nmtgg" not in low and "#nmtgg" not in low and " nmt " not in f" {low} ":
                    continue

                for code, context in extract_codes(haystack).items():
                    events.append(
                        event(
                            code,
                            f"YouTube tags/metadata: {title[:85]}",
                            url,
                            context,
                            upload_iso(item),
                            "youtube-tags",
                        )
                    )
        except Exception as exc:
            print(f"[SOCIAL WARN] YouTube {query}: {exc}")
    return events


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def is_recent(value, hours):
    dt = parse_iso(value)
    if not dt:
        return False
    age = (NOW - dt).total_seconds()
    return -3600 <= age <= hours * 3600


def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def existing_recent_codes(hours=12):
    if not REPO or not GITHUB_TOKEN:
        return set()
    r = HTTP.get(
        f"https://api.github.com/repos/{REPO}/issues",
        headers=github_headers(),
        params={"state": "all", "per_page": 100, "sort": "created", "direction": "desc"},
        timeout=20,
    )
    r.raise_for_status()
    cutoff = NOW.timestamp() - hours * 3600
    codes = set()
    for issue in r.json():
        title = issue.get("title") or ""
        if not title.startswith("🚨 NMT PROMO:"):
            continue
        created = parse_iso(issue.get("created_at"))
        if created and created.timestamp() >= cutoff:
            codes.add(title.split(":", 1)[1].strip().upper())
    return codes


def create_issue(code, grouped):
    if not REPO or not GITHUB_TOKEN:
        print(f"[SOCIAL ALERT NO-GITHUB] {code}")
        return

    lines = []
    for e in grouped[:5]:
        when = e.get("published_at") or "timestamp unavailable"
        lines.append(f"- **{e['source']}** — {when}\n  {e['url']}")

    body = (
        "## 🚨 NMT promo kodu — social watcher\n\n"
        f"# `{code}`\n\n"
        + "\n".join(lines)
        + "\n\n"
        f"**Yakalanma zamanı (UTC):** {NOW.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "X/YouTube sosyal taramasında bulundu. Kod limitli olabilir; NMT.GG üzerinde hızlı dene."
    )
    payload = {"title": f"🚨 NMT PROMO: {code}", "body": body}
    owner = REPO.split("/", 1)[0] if "/" in REPO else ""
    if owner:
        payload["assignees"] = [owner]

    r = HTTP.post(
        f"https://api.github.com/repos/{REPO}/issues",
        headers=github_headers(),
        json=payload,
        timeout=20,
    )
    r.raise_for_status()
    print(f"[SOCIAL ALERT] {code} {r.json().get('html_url', '')}")


def load_state():
    default = {"version": 1, "seen_event_ids": [], "last_alert": {}}
    if not STATE_PATH.exists():
        return default, True
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default, True
        data.setdefault("version", 1)
        data.setdefault("seen_event_ids", [])
        data.setdefault("last_alert", {})
        return data, False
    except Exception:
        return default, True


def save_state(state):
    state["updated_at"] = NOW.isoformat()
    state["seen_event_ids"] = state.get("seen_event_ids", [])[-8000:]
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fast", "deep"), default="fast")
    args = parser.parse_args()

    events = []
    try:
        events.extend(scan_official_x())
        print("[SOCIAL] official X scan ok")
    except Exception as exc:
        print(f"[SOCIAL WARN] official X: {exc}")

    events.extend(scan_x_search(deep=args.mode == "deep"))
    if args.mode == "deep":
        events.extend(scan_youtube_tags())

    events = list({e["id"]: e for e in events}.values())
    state, first_run = load_state()
    seen = set(state.get("seen_event_ids", []))
    last_alert = state.get("last_alert", {})

    if first_run:
        # Baseline untimestamped X/search evidence. Only very fresh timestamped YouTube
        # evidence may alert immediately on installation.
        grouped = defaultdict(list)
        for e in events:
            if e.get("kind") == "youtube-tags" and is_recent(e.get("published_at"), 12):
                grouped[e["code"]].append(e)
        recent_issues = existing_recent_codes(12)
        for code, group in grouped.items():
            if code not in recent_issues:
                create_issue(code, group)
                last_alert[code] = NOW.isoformat()
        seen.update(e["id"] for e in events)
        state["seen_event_ids"] = list(seen)
        state["last_alert"] = last_alert
        save_state(state)
        print(f"[SOCIAL BASELINE] events={len(events)}")
        return

    new_events = [e for e in events if e["id"] not in seen]
    grouped = defaultdict(list)
    for e in new_events:
        grouped[e["code"]].append(e)

    recent_issues = existing_recent_codes(12)
    alerted = 0
    for code, group in sorted(grouped.items()):
        if code in recent_issues:
            print(f"[SOCIAL DUPLICATE ISSUE] {code}")
            continue
        last = parse_iso(last_alert.get(code))
        if last and (NOW - last).total_seconds() < 12 * 3600:
            print(f"[SOCIAL DUPLICATE STATE] {code}")
            continue

        # Untimestamped X evidence is accepted when newly seen because its event id
        # is stable; first-run baselining prevents historical dumps.
        create_issue(code, group)
        last_alert[code] = NOW.isoformat()
        recent_issues.add(code)
        alerted += 1

    seen.update(e["id"] for e in new_events)
    state["seen_event_ids"] = list(seen)
    state["last_alert"] = last_alert
    save_state(state)
    print(f"[SOCIAL DONE] mode={args.mode} events={len(events)} new={len(new_events)} alerts={alerted}")


if __name__ == "__main__":
    main()
