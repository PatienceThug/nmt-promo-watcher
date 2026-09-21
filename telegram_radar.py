"""Read post-level evidence, including Telegram's copyable code blocks."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

REPORT = {}
NMT = re.compile(r'\bnmt(?:\.gg|\b)|\bnmtgg\b', re.I)


def post_codes(body, official, core):
    text = body.get_text(' ', strip=True)
    links = ' '.join(a.get('href', '') for a in body.select('a[href]'))
    if not official and not NMT.search(text + ' ' + links):
        return {}
    found = core.extract_codes(text)
    # Only isolated copyable tokens with an explicit promo context qualify.
    if core.KEYWORDS.search(text):
        for node in body.select('code, pre'):
            raw = node.get_text('', strip=True)
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{4,31}', raw):
                continue
            code = core.clean_code(raw)
            if code and (any(c.isdigit() for c in raw) or raw == raw.upper()):
                found[code] = text[:900]
    return found


def read_posts(url, core, page_limit=1):
    """Bounded backfill: stop at 48h, missing cursors, or repeated pages."""
    collected = {}
    next_url = url
    pages = 0
    for _ in range(max(1, min(page_limit, 3))):
        try:
            soup = BeautifulSoup(core.fetch_html(next_url), 'html.parser')
            posts = soup.select('div.tgme_widget_message')
            if not posts:
                raise RuntimeError('No readable Telegram posts')
        except Exception as exc:
            if not collected:
                raise
            return list(collected.values()), pages, str(exc)[:250]
        pages += 1
        ids = []
        fresh = False
        added = 0
        for post in posts:
            key = post.get('data-post', '')
            match = re.fullmatch(r'[A-Za-z0-9_]+/(\d+)', key)
            if match:
                ids.append(int(match.group(1)))
            else:
                key = str(post)
            if key not in collected:
                collected[key] = post
                added += 1
            time = post.select_one('time')
            dt = core.parse_iso(time.get('datetime')) if time else None
            if dt and (datetime.now(timezone.utc) - dt).total_seconds() <= 48 * 3600:
                fresh = True
        if not added or not ids or not fresh:
            break
        next_url = url + '?before=' + str(min(ids))
    return list(collected.values()), pages, None


def scan_telegram(name, channel, core):
    url = f'https://t.me/s/{channel}'
    health = {'url': url, 'status': 'error', 'posts': 0, 'candidates': 0}
    REPORT[name] = health
    try:
        posts, pages, warning = read_posts(url, core, int(os.environ.get('TELEGRAM_PAGE_LIMIT', '1')))
        health['pages'] = pages
        if warning:
            health['warning'] = warning
            print(f'[TELEGRAM BACKFILL] {name}: {warning}')
        events = []
        times = []
        for post in posts:
            health['posts'] += 1
            body = post.select_one('.tgme_widget_message_text')
            time = post.select_one('time')
            published = time.get('datetime') if time else None
            if published:
                times.append(published)
            if not body:
                continue
            link = post.select_one('a.tgme_widget_message_date')
            post_url = link.get('href') if link else url
            for code, context in post_codes(body, channel == 'nmt_official', core).items():
                events.append(core.make_event(code, name, post_url, context, published, 'telegram'))
        health.update(status='partial' if warning else 'ok', candidates=len(events), latest_post=max(times) if times else None)
        return events
    except Exception as exc:
        health['error'] = str(exc)[:250]
        raise


def write_report():
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'sources': REPORT}
    Path('scan_report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as out:
            out.write('## Telegram source coverage\n\nSource | Readable | Pages | Posts | Candidates | Latest post\n--- | --- | ---: | ---: | ---: | ---\n')
            for name, health in REPORT.items():
                out.write(f"{name} | {health['status']} | {health.get('pages', 0)} | {health['posts']} | {health['candidates']} | {health.get('latest_post') or 'unknown'}\n")
            out.write('\nCandidates are extracted evidence, not confirmed redeemable codes. Images/video pixels are not scanned.\n')
