"""Atomic, conflict-aware state commits via GitHub's Git Data API (no git push)."""
import base64
import json
import os
import time
from pathlib import Path

import requests
from merge_state import merge

FILES = ('seen_codes.json', 'social_state.json', 'social_state_v3.json', 'telegram_state.json')


def persist_files(incoming, request=None, sleep=time.sleep):
    repo = os.environ['GITHUB_REPOSITORY']
    token = os.environ['GITHUB_TOKEN']
    session = requests.Session()
    session.headers.update({'Authorization': f'Bearer {token}',
                            'Accept': 'application/vnd.github+json',
                            'X-GitHub-Api-Version': '2022-11-28'})
    def api(method, path, **kwargs):
        response = session.request(method, f'https://api.github.com/repos/{repo}{path}', timeout=25, **kwargs)
        response.raise_for_status()
        return response.json()
    api = request or api
    for attempt in range(4):
        try:
            head = api('GET', '/git/ref/heads/main')['object']['sha']
            base = api('GET', f'/git/commits/{head}')['tree']['sha']
            entries = []
            for name, value in incoming.items():
                try:
                    remote = api('GET', f'/contents/{name}', params={'ref': head})
                    old = json.loads(base64.b64decode(remote['content']))
                except requests.HTTPError as exc:
                    if exc.response is None or exc.response.status_code != 404:
                        raise
                    old = {}
                combined = merge(old, value)
                if combined != old:
                    entries.append({'path': name, 'mode': '100644', 'type': 'blob',
                                    'content': json.dumps(combined, indent=2, ensure_ascii=False) + '\n'})
            if not entries:
                return
            tree = api('POST', '/git/trees', json={'base_tree': base, 'tree': entries})['sha']
            commit = api('POST', '/git/commits', json={'message': 'Update NMT watcher state',
                         'tree': tree, 'parents': [head]})['sha']
            api('PATCH', '/git/refs/heads/main', json={'sha': commit, 'force': False})
            print(f'[STATE] Atomically saved {len(entries)} file(s) via API')
            return
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (401, 403) or attempt == 3:
                raise
            print(f'[STATE] Retry {attempt + 1}/4 (HTTP {status or "network"})')
            sleep(2 ** (attempt + 1))


def main():
    incoming = {name: json.loads(Path(name).read_text()) for name in FILES if Path(name).exists()}
    persist_files(incoming)


if __name__ == '__main__':
    main()
