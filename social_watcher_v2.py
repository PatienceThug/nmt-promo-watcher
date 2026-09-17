import os
import re
import requests
import social_watcher as s

X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "").strip()

# Turkish phrase support + broader social discovery terms.
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


def x_api_search(query, label):
    if not X_BEARER_TOKEN:
        return []
    try:
        r = requests.get(
            "https://api.x.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
            params={
                "query": query,
                "max_results": 100,
                "tweet.fields": "created_at,author_id",
            },
            timeout=25,
        )
        if r.status_code in (401, 403, 429):
            print(f"[SOCIAL WARN] X API {label}: HTTP {r.status_code}")
            return []
        r.raise_for_status()
        events = []
        for post in (r.json().get("data") or []):
            text = post.get("text") or ""
            post_id = post.get("id") or ""
            url = f"https://x.com/i/web/status/{post_id}" if post_id else "https://x.com/"
            for code, context in s.extract_codes(text).items():
                events.append(
                    s.event(
                        code,
                        f"X API: {label}",
                        url,
                        context,
                        post.get("created_at"),
                        "x-api",
                    )
                )
        print(f"[SOCIAL] X API {label}: {len(events)} promo event(s)")
        return events
    except Exception as exc:
        print(f"[SOCIAL WARN] X API {label}: {exc}")
        return []


_original_official = s.scan_official_x
_original_search = s.scan_x_search


def scan_official_with_api():
    events = []
    try:
        events.extend(_original_official())
    except Exception as exc:
        print(f"[SOCIAL WARN] X official fallback: {exc}")
    events.extend(
        x_api_search(
            'from:nmt_off ("nmt.gg" OR promo OR promocode OR promokod OR "promo kod" OR "promosyon kodu" OR промокод) -is:retweet',
            "official @nmt_off",
        )
    )
    return events


def scan_search_with_api(deep=False):
    events = _original_search(deep=deep)
    if X_BEARER_TOKEN:
        events.extend(
            x_api_search(
                '("nmt.gg" OR #NMTGG OR @nmt_off) (promo OR promocode OR promokod OR "promo kod" OR "promosyon kodu" OR промокод) -is:retweet',
                "NMT public mentions",
            )
        )
    return events


s.scan_official_x = scan_official_with_api
s.scan_x_search = scan_search_with_api

if __name__ == "__main__":
    s.main()
