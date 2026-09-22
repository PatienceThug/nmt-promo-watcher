"""Atomic, conflict-aware state commits via GitHub's Git Data API (no git push)."""
import base64
import json
import os
import time
import sys
from pathlib import Path
from datetime import datetime, timezone

import requests
from merge_state import merge

FILES = ('seen_codes.json', 'youtube_state.json', 'telegram_state.json', 'nmt_brain_state.json')



def _parse_ts(value):
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _merge_rows_by_id(old_rows, new_rows):
    rows = {}
    order = []
    for row in list(old_rows or []) + list(new_rows or []):
        key = str(row.get("id", ""))
        if not key:
            key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key not in rows:
            order.append(key)
        rows[key] = row
    return [rows[k] for k in order]


def merge_brain_state(old, incoming):
    """Event lists are unioned; mutable settings/profile come from newest state."""
    old = old if isinstance(old, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    old_ts = _parse_ts(old.get("updated_at"))
    new_ts = _parse_ts(incoming.get("updated_at"))
    newest = incoming if new_ts >= old_ts else old

    result = dict(old)
    result.update(newest)
    result["version"] = max(int(old.get("version", 1)), int(incoming.get("version", 1)))
    result["initialized"] = bool(old.get("initialized") or incoming.get("initialized"))
    result["last_update_id"] = max(int(old.get("last_update_id", 0)), int(incoming.get("last_update_id", 0)))
    result["ledger"] = _merge_rows_by_id(old.get("ledger", []), incoming.get("ledger", []))
    result["power_rounds"] = _merge_rows_by_id(old.get("power_rounds", []), incoming.get("power_rounds", []))
    return result

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
                combined = merge_brain_state(old, value) if name == "nmt_brain_state.json" else merge(old, value)
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
    requested = tuple(sys.argv[1:]) if len(sys.argv) > 1 else FILES
    unknown = [name for name in requested if name not in FILES]
    if unknown:
        raise SystemExit(f"unsupported state file(s): {', '.join(unknown)}")
    incoming = {name: json.loads(Path(name).read_text()) for name in requested if Path(name).exists()}
    if not incoming:
        print("[STATE] Nothing to persist")
        return
    persist_files(incoming)


if __name__ == '__main__':
    main()
