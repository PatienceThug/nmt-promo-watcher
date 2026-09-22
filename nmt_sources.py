"""Explicit, bounded public Academy checks. Never treats documentation as live prices.

Run manually. An HTTP success is only a fetched document, not verified mechanics.
Prior successful fingerprints survive blocked/failed checks for comparison.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import tempfile

import requests
from bs4 import BeautifulSoup

GUIDES = {key: f'https://nmt.gg/en/academy/{key}' for key in
          ('power-blocks', 'collections', 'marketplace', 'power', 'levels',
           'merge', 'token', 'power-pool', 'lucky-buy', 'explorer')}
MAX_BYTES = 2_000_000


def check(url, previous=None, session=None):
    previous = previous or {}
    result = {**previous, 'source': url,
              'checked_at': datetime.now(timezone.utc).isoformat()}
    session = session or requests
    try:
        with session.get(url, timeout=(5, 10), stream=True, allow_redirects=False,
                         headers={'User-Agent': 'NMT-Research-Monitor/1.0'}) as response:
            result['http_status'] = response.status_code
            if response.status_code != 200:
                result['status'] = 'blocked' if response.status_code in (401, 403, 429) else 'error'
                return result
            parts, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Kaynak boyut sınırını aştı.')
                parts.append(chunk)
        soup = BeautifulSoup(b''.join(parts), 'html.parser')
        for node in soup(['script', 'style', 'nav', 'footer']):
            node.decompose()
        body = soup.find('main') or soup
        content = ' '.join(body.stripped_strings)
        if (len(content) < 300 or not body.find(['h1', 'h2'])
                or any(x in content.lower() for x in ('just a moment', 'verify you are human', 'access denied'))):
            result['status'] = 'unreadable'
            return result
        digest = hashlib.sha256(content.encode()).hexdigest()
        old = previous.get('last_success_hash')
        # Once a change is seen, keep it visible until explicitly reviewed.
        pending = previous.get('change_pending', False) or (old is not None and old != digest)
        result.update(status='changed' if pending else 'fetched', change_pending=pending,
                      last_success_hash=digest, last_success_at=result['checked_at'],
                      text=content, verification='unreviewed')
        result.pop('error', None)
    except (requests.RequestException, ValueError) as exc:
        result.update(status='error', error=type(exc).__name__)
    return result


def save_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.nmt-source-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='nmt_source_checks.json')
    parser.add_argument('--guide', choices=GUIDES, action='append')
    args = parser.parse_args()
    path = Path(args.output)
    data = json.loads(path.read_text()) if path.exists() else {'schema_version': 1, 'guides': {}}
    with requests.Session() as session:
        for name in args.guide or GUIDES:
            item = check(GUIDES[name], data['guides'].get(name), session)
            data['guides'][name] = item
            save_atomic(path, data)
            print(f"{name}: {item['status']}", flush=True)


if __name__ == '__main__':
    main()
