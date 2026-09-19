import email.utils
import json
import re
import subprocess
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
import social_watcher as s

# FREE MODE ONLY: no paid X API calls are made from this watcher.
# Official X is read from X's public syndication timeline when available;
# public mentions are discovered through free web indexes.

s.X_QUERIES_FAST = list(dict.fromkeys(s.X_QUERIES_FAST + [
    'site:x.com/nmt_off "promosyon kodu"',
]))
s.X_QUERIES_DEEP = list(dict.fromkeys(s.X_QUERIES_DEEP + [
    'site:x.com "nmt.gg" "promosyon kodu"',
    'site:x.com "#NMTGG" "promosyon kodu"',
    'site:x.com "@nmt_off" "promosyon kodu"',
]))
s.YOUTUBE_QUERIES = list(dict.fromkeys(s.YOUTUBE_QUERIES + [
    "NMT.GG promosyon kodu",
    "#NMTGG promosyon kodu",
]))

s.PROMO_WORDS = re.compile(
    r"(promo\s*code|promocode|promo\s*kod\w*|promokod\w*|promosyon\s*kod\w*|"
    r"промокод\w*|промо\s*код\w*|промо-код\w*|"
    r"bonus\s*code|gift\s*code|hediye\s*kod\w*|voucher\s*code|coupon\s*code)",
    re.I,
)

s.DIRECT_PATTERNS = [
    re.compile(
        r"(?:promo\s*code|promocode|promo\s*kod\w*|promokod\w*|promosyon\s*kod\w*|"
        r"промокод\w*|промо\s*код\w*|промо-код\w*|"
        r"bonus\s*code|gift\s*code|hediye\s*kod\w*|voucher\s*code|coupon\s*code)"
        r"\s*[:=/#\-–—]*\s*[`\"'“”‘’]*([A-Z0-9][A-Z0-9_\-]{4,31})",
        re.I,
    ),
    re.compile(
        r"([A-Z0-9][A-Z0-9_\-]{4,31})[`\"'“”‘’]*\s*"
        r"(?:promo\s*code|promocode|promokod|промокод|promo\s*kod\w*|promosyon\s*kod\w*|hediye\s*kod\w*)",
        re.I,
    ),
]

SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/nmt_off"
LAST_X_HEALTH = {}


def _retry_after_seconds(response, default=1800):
    """Return a bounded Retry-After delay without trying to evade X limits."""
    value = (response.headers.get("Retry-After") or "").strip()
    try:
        seconds = int(value)
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = int((retry_at - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            seconds = default
    return max(300, min(seconds, 6 * 3600))


def _twitter_time(value):
    if not value:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def _tweet_objects(obj):
    if isinstance(obj, dict):
        legacy = obj.get("legacy")
        if isinstance(legacy, dict) and legacy.get("full_text"):
            yield legacy, str(obj.get("rest_id") or legacy.get("id_str") or "")
        elif obj.get("full_text"):
            yield obj, str(obj.get("id_str") or obj.get("id") or "")
        for value in obj.values():
            yield from _tweet_objects(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _tweet_objects(value)


def _scan_public_reader():
    text = s.fetch_text(
        s.JINA_X_URL,
        headers={"X-Cache-Tolerance": "0", "X-Retain-Images": "none"},
        timeout=35,
    )
    if "nmt" not in text.lower():
        raise RuntimeError("official X reader returned unexpected content")
    events = []
    for code, context in s.extract_codes(text).items():
        events.append(
            s.event(
                code,
                "X official @nmt_off (public reader)",
                s.OFFICIAL_X_URL,
                context,
                None,
                "x-reader",
            )
        )
    print(f"[SOCIAL] public X reader: {len(events)} promo event(s)")
    return events


def scan_official_x_free(skip_syndication=False):
    LAST_X_HEALTH.clear()
    LAST_X_HEALTH["checked_at"] = datetime.now(timezone.utc).isoformat()
    if skip_syndication:
        LAST_X_HEALTH.update(primary="cooldown", fallback="pending")
        try:
            events = _scan_public_reader()
            LAST_X_HEALTH["fallback"] = "ok"
            return events
        except Exception as exc:
            LAST_X_HEALTH.update(fallback="error", error=str(exc)[:240])
            raise
    try:
        r = requests.get(
            SYNDICATION_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36"
            },
            timeout=25,
        )
        if r.status_code == 429:
            LAST_X_HEALTH["retry_after_seconds"] = _retry_after_seconds(r)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            raise RuntimeError("X syndication page missing __NEXT_DATA__")
        data = json.loads(script.string)

        events = []
        now = datetime.now(timezone.utc)
        seen_ids = set()
        for legacy, post_id in _tweet_objects(data):
            text = legacy.get("full_text") or ""
            if post_id and post_id in seen_ids:
                continue
            if post_id:
                seen_ids.add(post_id)
            published_dt = _twitter_time(legacy.get("created_at"))
            if not published_dt or (now - published_dt).total_seconds() > 12 * 3600:
                continue
            url = f"https://x.com/nmt_off/status/{post_id}" if post_id else "https://x.com/nmt_off"
            for code, context in s.extract_codes(text).items():
                events.append(
                    s.event(
                        code,
                        "X official @nmt_off (free syndication)",
                        url,
                        context,
                        published_dt.isoformat(),
                        "x-syndication",
                    )
                )
        print(f"[SOCIAL] free X syndication: {len(events)} promo event(s)")
        LAST_X_HEALTH.update(primary="ok", fallback="not_needed")
        return events
    except Exception as exc:
        # X syndication is frequently rate-limited. The public Jina reader keeps
        # discovery alive without a paid X API. Its untimestamped evidence is
        # intentionally not considered fresh unless another source confirms it.
        print(f"[SOCIAL FALLBACK] X syndication unavailable: {exc}")
        LAST_X_HEALTH.setdefault("primary", "rate_limited" if "429" in str(exc) else "error")
        LAST_X_HEALTH["error"] = str(exc)[:240]
        try:
            events = _scan_public_reader()
            LAST_X_HEALTH["fallback"] = "ok"
            return events
        except Exception as fallback_exc:
            LAST_X_HEALTH.update(fallback="error", fallback_error=str(fallback_exc)[:240])
            raise


_original_search = s.scan_x_search


def scan_search_free(deep=False):
    # Free discovery of public X posts through web indexes. No X API credits used.
    return _original_search(deep=deep)


def scan_youtube_tags_fixed():
    events = []
    seen_video_ids = set()
    for query in s.YOUTUBE_QUERIES:
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
                for code, context in s.extract_codes(haystack).items():
                    events.append(
                        s.event(
                            code,
                            f"YouTube tags/metadata: {title[:85]}",
                            url,
                            context,
                            published,
                            "youtube-tags",
                        )
                    )
            if proc.returncode not in (0, 1):
                print(f"[SOCIAL WARN] YouTube {query}: return {proc.returncode}")
        except Exception as exc:
            print(f"[SOCIAL WARN] YouTube {query}: {exc}")
    return events


s.scan_official_x = scan_official_x_free
s.scan_x_search = scan_search_free
s.scan_youtube_tags = scan_youtube_tags_fixed

if __name__ == "__main__":
    s.main()
