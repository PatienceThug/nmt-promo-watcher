import json
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

REPO = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
STATE_PATH = Path("seen_codes.json")

SOURCES = {
    "NMT Official Telegram": "https://t.me/s/nmt_official",
    "Kripto Master": "https://t.me/s/kriptomastr",
    "Cryptanchan": "https://t.me/s/cryptanchan",
    "NMT News": "https://nmt.gg/en/news",
    "NMT Academy": "https://nmt.gg/en/academy",
}

UA = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Safari/537.36"
}

KEYWORDS = re.compile(
    r"(promo\s*code|promocode|promo\s*kod\w*|promokod\w*|промокод\w*|промо\s*код\w*|bonus\s*code|gift\s*code|free\s*case)",
    re.I,
)

DIRECT_PATTERNS = [
    re.compile(
        r"(?:promo\s*code|promocode|promo\s*kod\w*|promokod\w*|промокод\w*|промо\s*код\w*)\s*[:=\-–—]*\s*[`\"']*([A-Z0-9]{5,24})",
        re.I,
    ),
    re.compile(
        r"([A-Z0-9]{5,24})[`\"']*\s*(?:promo\s*code|promocode|promokod|промокод)",
        re.I,
    ),
]

BAD = {
    "PROMOCODE", "PROMOKOD", "WELCOME", "TELEGRAM", "YOUTUBE",
    "BONUSCODE", "GIFTCODE", "POWERBLOCKS", "DISCORD", "INSTAGRAM",
}


def extract_codes(text: str):
    text = " ".join(text.split())
    found = set()

    for pattern in DIRECT_PATTERNS:
        for code in pattern.findall(text):
            code = code.upper().strip()
            if code not in BAD:
                found.add(code)

    for match in KEYWORDS.finditer(text):
        window = text[max(0, match.start() - 120): match.end() + 260]
        for code in re.findall(r"\b[A-Z0-9]{6,24}\b", window.upper()):
            if code in BAD:
                continue
            if any(c.isalpha() for c in code) and any(c.isdigit() for c in code):
                found.add(code)

    return found


def fetch_text(url: str):
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)


def scan_pages():
    results = {}
    for name, url in SOURCES.items():
        try:
            text = fetch_text(url)
            for code in extract_codes(text):
                results[code] = {"source": name, "url": url}
        except Exception as e:
            print(f"[WARN] {name}: {e}")
    return results


def scan_youtube():
    results = {}
    queries = [
        "NMT.GG promo code",
        "NMT GG promocode",
        "NMT promo kod",
    ]

    for query in queries:
        try:
            proc = subprocess.run(
                [
                    "yt-dlp", "--skip-download", "--dump-json",
                    "--playlist-end", "10", f"ytsearchdate10:{query}"
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            for line in proc.stdout.splitlines():
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                title = item.get("title") or ""
                desc = item.get("description") or ""
                url = item.get("webpage_url") or item.get("original_url") or ""
                for code in extract_codes(title + "\n" + desc):
                    results[code] = {"source": f"YouTube: {title[:90]}", "url": url}
        except Exception as e:
            print(f"[WARN] YouTube query {query}: {e}")
    return results


def load_seen():
    if not STATE_PATH.exists():
        return set(), True
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return set(data.get("codes", [])), False
    except Exception:
        return set(), True


def save_seen(codes):
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "codes": sorted(codes),
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def create_issue(code, info):
    if not REPO or not GITHUB_TOKEN:
        print("[WARN] GitHub issue skipped: token/repo missing")
        return

    api = f"https://api.github.com/repos/{REPO}/issues"
    body = (
        f"## 🚨 Yeni NMT promo kodu\n\n"
        f"**Kod:** `{code}`\n\n"
        f"**Kaynak:** {info['source']}\n\n"
        f"**Bağlantı:** {info['url']}\n\n"
        f"**Bulunma zamanı (UTC):** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "Kod sınırlı kullanımlı olabilir; mümkün olduğunca hızlı dene."
    )
    r = requests.post(
        api,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"title": f"🚨 NMT PROMO: {code}", "body": body},
        timeout=25,
    )
    r.raise_for_status()
    print(f"[ALERT] issue created for {code}")


def main():
    found = {}
    found.update(scan_pages())
    found.update(scan_youtube())

    seen, first_run = load_seen()
    current_codes = set(found.keys())

    if first_run:
        seen.update(current_codes)
        save_seen(seen)
        print(f"[BASELINE] stored {len(current_codes)} existing codes; no alerts sent")
        return

    new_codes = [code for code in sorted(current_codes) if code not in seen]
    for code in new_codes:
        create_issue(code, found[code])
        seen.add(code)

    if new_codes:
        save_seen(seen)
        print(f"[DONE] alerted on {len(new_codes)} new code(s)")
    else:
        print("[DONE] no new codes")


if __name__ == "__main__":
    main()
