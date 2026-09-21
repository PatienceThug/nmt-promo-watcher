import json
import re
import subprocess
from datetime import datetime, timezone

import watcher_v3 as w
import telegram_radar as radar

# Extend the core watcher with the Turkish phrase "promosyon kodu" without
# duplicating the stable v3 implementation.
w.SEARCH_QUERIES = list(dict.fromkeys(w.SEARCH_QUERIES + [
    '"nmt.gg" "promosyon kodu"',
    '"NMT" "promosyon kodu"',
]))
w.YOUTUBE_QUERIES = list(dict.fromkeys(w.YOUTUBE_QUERIES + [
    "NMT.GG promosyon kodu",
]))

w.KEYWORDS = re.compile(
    r"(promo\s*code|promocode|promo\s*kod\w*|promokod\w*|promosyon\s*kod\w*|"
    r"промокод\w*|промо\s*код\w*|промо-код\w*|"
    r"bonus\s*code|gift\s*code|hediye\s*kod\w*|hediye\s*code|"
    r"free\s*case|ücretsiz\s*case|coupon\s*code|voucher\s*code)",
    re.I,
)

w.DIRECT_PATTERNS = [
    re.compile(
        r"(?:promo\s*code|promocode|promo\s*kod\w*|promokod\w*|promosyon\s*kod\w*|"
        r"промокод\w*|промо\s*код\w*|промо-код\w*|"
        r"bonus\s*code|gift\s*code|hediye\s*kod\w*|hediye\s*code)"
        r"\s*[:=/#\-–—]*\s*[`\"'“”‘’]*([A-Z0-9][A-Z0-9_\-]{4,31})",
        re.I,
    ),
    re.compile(
        r"([A-Z0-9][A-Z0-9_\-]{4,31})[`\"'“”‘’]*\s*"
        r"(?:promo\s*code|promocode|promokod|промокод|promo\s*kod\w*|promosyon\s*kod\w*|hediye\s*kod\w*)",
        re.I,
    ),
]


def scan_youtube_fixed():
    events = []
    for query in w.YOUTUBE_QUERIES:
        try:
            proc = subprocess.run(
                [
                    "yt-dlp", "--skip-download", "--dump-json",
                    "--playlist-end", "8", f"ytsearch8:{query}",
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
                ts = item.get("timestamp")
                if ts:
                    try:
                        published = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                    except Exception:
                        pass
                if not published:
                    upload_date = item.get("upload_date")
                    if upload_date and len(upload_date) == 8:
                        try:
                            published = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
                        except Exception:
                            pass
                for code, context in w.extract_codes(title + "\n" + desc).items():
                    events.append(
                        w.make_event(
                            code,
                            f"YouTube: {title[:90]}",
                            url,
                            context,
                            published,
                            "youtube",
                        )
                    )
            if proc.returncode not in (0, 1):
                print(f"[WARN] YouTube {query}: return {proc.returncode}")
        except Exception as exc:
            print(f"[WARN] YouTube {query}: {exc}")
    return events


w.scan_youtube = scan_youtube_fixed

w.scan_telegram = lambda name, channel: radar.scan_telegram(name, channel, w)

if __name__ == "__main__":
    try:
        w.main()
    finally:
        radar.write_report()
