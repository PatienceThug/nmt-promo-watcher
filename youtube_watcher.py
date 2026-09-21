"""YouTube-only discovery. No social account or login is used."""
import argparse
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import requests
import watcher_v4
w = watcher_v4.w
NOW = datetime.now(timezone.utc)
STATE_PATH = Path("youtube_state.json")
REPO = w.REPO
GITHUB_TOKEN = w.GITHUB_TOKEN
HTTP = w.HTTP
UA = w.UA
YOUTUBE_QUERIES = ["NMT.GG promo code", "NMT.GG promosyon kodu", "NMT GG промокод", "NMT.GG shorts"]
parse_iso = w.parse_iso
extract_codes = w.extract_codes
event = w.make_event

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
        "## 🚨 NMT promo kodu — YouTube watcher\n\n"
        f"# `{code}`\n\n"
        + "\n".join(lines)
        + "\n\n"
        f"**Yakalanma zamanı (UTC):** {NOW.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "YouTube taramasında bulundu. Kod limitli olabilir; NMT.GG üzerinde hızlı dene."
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
def is_recent(value, hours):
    dt = parse_iso(value)
    if not dt:
        return False
    age = (NOW - dt).total_seconds()
    return -3600 <= age <= hours * 3600

def item_time(item):
    ts = item.get("timestamp")
    if ts:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        except Exception:
            pass
    value = item.get("upload_date")
    if value and len(value) == 8:
        try:
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    return None

def relevant_youtube_text(item):
    title = item.get("title") or ""
    desc = item.get("description") or ""
    tags = item.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    channel = item.get("channel") or item.get("uploader") or ""
    return "\n".join([title, desc, " ".join(str(x) for x in tags), channel])

def is_nmt_related(text):
    low = (text or "").lower()
    return (
        "nmt.gg" in low
        or "nmtgg" in low
        or "#nmtgg" in low
        or "@nmt_off" in low
        or " nmt " in f" {low} "
    )

def _decode_json_string(value):
    try:
        return json.loads('"' + value + '"')
    except Exception:
        return value.replace("\\n", " ").replace("\\u0026", "&")

def discover_youtube_html(query, limit=8):
    """Free fallback when yt-dlp/YouTube search yields no rows on cloud IPs."""
    search_url = "https://www.youtube.com/results"
    r = requests.get(
        search_url,
        params={"search_query": query, "sp": "CAI%3D"},
        headers=UA,
        timeout=25,
    )
    r.raise_for_status()
    ids = list(dict.fromkeys(re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', r.text)))
    items = []
    for video_id in ids[:limit]:
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            page = requests.get(url, headers=UA, timeout=20)
            page.raise_for_status()
            body = page.text
            title_m = re.search(r'"title":"((?:\\\\.|[^"\\\\])*)"', body)
            desc_m = re.search(r'"shortDescription":"((?:\\\\.|[^"\\\\])*)"', body)
            date_m = re.search(r'"publishDate":"(\d{4}-\d{2}-\d{2})"', body)
            title = _decode_json_string(title_m.group(1)) if title_m else ""
            desc = _decode_json_string(desc_m.group(1)) if desc_m else ""
            published = None
            if date_m:
                published = datetime.strptime(date_m.group(1), "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                ).isoformat()
            items.append(
                {
                    "id": video_id,
                    "webpage_url": url,
                    "title": title,
                    "description": desc,
                    "timestamp": (
                        datetime.fromisoformat(published).timestamp() if published else None
                    ),
                }
            )
        except Exception as exc:
            print(f"[V3 WARN] YouTube HTML video {video_id}: {exc}")
    print(f"[V3 FALLBACK] YouTube HTML query={query!r} candidates={len(items)}")
    return items

def discover_youtube_items():
    items = {}
    for query in YOUTUBE_QUERIES:
        try:
            proc = subprocess.run(
                [
                    "yt-dlp", "--skip-download", "--dump-json", "--no-warnings",
                    "--playlist-end", "6", f"ytsearch6:{query}",
                ],
                capture_output=True,
                text=True,
                timeout=70,
            )
            for line in proc.stdout.splitlines():
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                video_id = str(item.get("id") or "")
                url = item.get("webpage_url") or item.get("original_url") or ""
                key = video_id or url
                if not key:
                    continue
                if not is_nmt_related(relevant_youtube_text(item)):
                    continue
                items[key] = item
        except Exception as exc:
            print(f"[V3 WARN] YouTube discovery {query}: {exc}")

    if not items:
        for query in YOUTUBE_QUERIES:
            try:
                for item in discover_youtube_html(query):
                    key = str(item.get("id") or item.get("webpage_url") or "")
                    if key and is_nmt_related(relevant_youtube_text(item)):
                        items[key] = item
            except Exception as exc:
                print(f"[V3 WARN] YouTube HTML discovery {query}: {exc}")

    def sort_key(item):
        dt = parse_iso(item_time(item))
        return dt.timestamp() if dt else 0

    return sorted(items.values(), key=sort_key, reverse=True)

def youtube_metadata_events(items):
    events = []
    for item in items:
        text = relevant_youtube_text(item)
        title = item.get("title") or ""
        url = item.get("webpage_url") or item.get("original_url") or ""
        published = item_time(item)
        duration = item.get("duration")
        is_short = False
        try:
            is_short = duration is not None and float(duration) <= 180
        except Exception:
            pass
        kind = "youtube-shorts" if is_short else "youtube-tags"
        label = "YouTube Shorts metadata" if is_short else "YouTube tags/metadata"
        for code, context in extract_codes(text).items():
            events.append(event(code, f"{label}: {title[:85]}", url, context, published, kind))
    return events

def youtube_comment_events(items, max_videos=5):
    events = []
    # Comments are expensive to fetch, so only check the most relevant/recent small set.
    for item in items[:max_videos]:
        title = item.get("title") or ""
        url = item.get("webpage_url") or item.get("original_url") or ""
        if not url:
            continue
        try:
            proc = subprocess.run(
                [
                    "yt-dlp", "--skip-download", "--write-comments", "--dump-single-json", "--no-warnings",
                    "--extractor-args", "youtube:comment_sort=new;max_comments=40,40,0,0,1",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if not proc.stdout.strip():
                if proc.stderr.strip():
                    print(f"[V3 WARN] comments {title[:45]}: {proc.stderr.splitlines()[-1][:180]}")
                continue
            try:
                info = json.loads(proc.stdout)
            except Exception:
                print(f"[V3 WARN] comments JSON {title[:45]}")
                continue

            comments = info.get("comments") or []
            checked = 0
            for comment in comments:
                if checked >= 40:
                    break
                checked += 1
                text = comment.get("text") or ""
                if not text:
                    continue
                ts = comment.get("timestamp")
                published = None
                if ts:
                    try:
                        published = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                    except Exception:
                        pass
                # Promo codes are time-sensitive; old comments are deliberately ignored.
                if not published or not is_recent(published, 72):
                    continue
                comment_id = str(comment.get("id") or "")
                comment_url = url
                if comment_id:
                    sep = "&" if "?" in url else "?"
                    comment_url = f"{url}{sep}lc={comment_id}"
                for code, context in extract_codes(text).items():
                    events.append(
                        event(
                            code,
                            f"YouTube yeni yorum: {title[:80]}",
                            comment_url,
                            context,
                            published,
                            "youtube-comment",
                        )
                    )
            print(f"[V3] comments checked={checked} video={title[:55]}")
        except subprocess.TimeoutExpired:
            print(f"[V3 WARN] comments timeout: {title[:55]}")
        except Exception as exc:
            print(f"[V3 WARN] comments {title[:45]}: {exc}")
    return events

def save_state(state):
    state["updated_at"] = NOW.isoformat()
    state["seen_event_ids"] = state.get("seen_event_ids", [])[-10000:]
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fast", "deep"), default="fast")
    args = parser.parse_args()

    state = load_state()
    events = scan(args.mode, state)
    seen = set(state.get("seen_event_ids", []))
    last_alert = state.get("last_alert", {})

    if not state.get("initialized"):
        # Baseline all newly-added mirror/search surfaces so historical codes do not spam.
        # Only fresh timestamped evidence may alert on the upgrade run.
        grouped = defaultdict(list)
        for e in events:
            if strong_fresh([e]):
                grouped[e["code"]].append(e)
        recent_issues = existing_recent_codes(12)
        alerts = 0
        for code, group in grouped.items():
            if code in recent_issues:
                continue
            create_issue(code, group)
            last_alert[code] = NOW.isoformat()
            alerts += 1
        seen.update(e["id"] for e in events)
        state["initialized"] = True
        state["seen_event_ids"] = list(seen)
        state["last_alert"] = last_alert
        save_state(state)
        print(f"[V3 BASELINE] events={len(events)} fresh_alerts={alerts}")
        return

    new_events = [e for e in events if e["id"] not in seen]
    grouped = defaultdict(list)
    for e in new_events:
        grouped[e["code"]].append(e)

    recent_issues = existing_recent_codes(12)
    alerted = 0
    for code, group in sorted(grouped.items()):
        if code in recent_issues:
            print(f"[V3 DUPLICATE ISSUE] {code}")
            continue
        last = parse_iso(last_alert.get(code))
        if last and (NOW - last).total_seconds() < 12 * 3600:
            print(f"[V3 DUPLICATE STATE] {code}")
            continue
        if not eligible(group):
            print(f"[V3 HOLD] {code} weak single-source evidence")
            continue
        create_issue(code, group)
        last_alert[code] = NOW.isoformat()
        recent_issues.add(code)
        alerted += 1

    seen.update(e["id"] for e in new_events)
    state["seen_event_ids"] = list(seen)
    state["last_alert"] = last_alert
    save_state(state)
    print(f"[V3 DONE] mode={args.mode} events={len(events)} new={len(new_events)} alerts={alerted}")

def load_state():
    # Migrate delivery history once, without carrying retired source health forward.
    source = STATE_PATH if STATE_PATH.exists() else Path("social_state_v3.json")
    if not source.exists():
        return {"initialized": False, "seen_event_ids": [], "last_alert": {}}
    data = json.loads(source.read_text())
    return {k: data[k] for k in ("initialized", "seen_event_ids", "last_alert") if k in data}


def strong_fresh(group):
    return any(e.get("kind") in ("youtube-comment", "youtube-tags", "youtube-shorts")
               and is_recent(e.get("published_at"), 72) for e in group)

eligible = strong_fresh


def scan(mode, state):
    if mode != "deep":
        print("[YouTube] Deep discovery scheduled for a later run")
        return []
    items = discover_youtube_items()
    events = youtube_metadata_events(items) + youtube_comment_events(items, max_videos=3)
    print(f"[YouTube] relevant_videos={len(items)} candidates={len(events)}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as out:
            out.write(f"\n## YouTube\n\nRelevant videos: {len(items)}; candidates: {len(events)}. Zero results do not prove complete coverage.\n")
    return list({e["id"]: e for e in events}.values())

if __name__ == "__main__":
    main()
