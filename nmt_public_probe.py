"""Read-only NMT public-data probe used while building NMT Brain.

No account login, cookies, wallet, or gameplay requests are used.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

URLS = [
    "https://nmt.gg/en/explorer",
    "https://nmt.gg/ru/explorer?q=%7Bsearch_term_string%7D",
    "https://nmt.gg/en",
    "https://nmt.gg/power-blocks",
]
UA = {"User-Agent": "Mozilla/5.0 (NMT-Brain-ReadOnly/1.0)"}
TERMS = re.compile(r"round|power blocks|settled|started|winner|reward", re.I)


def fetch(url):
    r = requests.get(url, headers=UA, timeout=25)
    if r.status_code == 403 and url.startswith("https://nmt.gg/"):
        fallback = "https://r.jina.ai/http://" + url.removeprefix("https://")
        r = requests.get(
            fallback,
            headers={"X-Cache-Tolerance": "0", "X-Retain-Images": "none"},
            timeout=40,
        )
        r.raise_for_status()
        return r.text, fallback
    r.raise_for_status()
    return r.text, url


def snippets(text, limit=30):
    compact = re.sub(r"\s+", " ", text)
    found = []
    for match in TERMS.finditer(compact):
        s = max(0, match.start() - 220)
        e = min(len(compact), match.end() + 420)
        piece = compact[s:e].strip()
        if piece not in found:
            found.append(piece)
        if len(found) >= limit:
            break
    return found


def main():
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sources": [],
    }
    for url in URLS:
        row = {"url": url}
        try:
            body, effective = fetch(url)
            row.update(
                status="ok",
                effective_url=effective,
                bytes=len(body.encode("utf-8", "ignore")),
                snippets=snippets(body),
            )
            print(f"[PROBE] {url} -> {effective} bytes={row['bytes']} hits={len(row['snippets'])}")
            for i, piece in enumerate(row["snippets"][:12], 1):
                print(f"[PROBE:{i}] {piece[:900]}")
        except Exception as exc:
            row.update(status="error", error=str(exc)[:500])
            print(f"[PROBE] {url} ERROR {row['error']}")
        report["sources"].append(row)

    Path("nmt_probe.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
