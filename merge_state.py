import json
import sys
from pathlib import Path


def merge(base, incoming):
    if isinstance(base, dict) and isinstance(incoming, dict):
        result = dict(base)
        for key, value in incoming.items():
            result[key] = merge(result[key], value) if key in result else value
        return result
    if isinstance(base, list) and isinstance(incoming, list):
        result = list(base)
        for value in incoming:
            if value not in result:
                result.append(value)
        return result
    return incoming


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: merge_state.py CURRENT INCOMING")
    current = Path(sys.argv[1])
    incoming = Path(sys.argv[2])
    if not incoming.exists():
        return
    try:
        right = json.loads(incoming.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"invalid incoming state {incoming}: {exc}")
    left = {}
    if current.exists():
        try:
            left = json.loads(current.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"invalid current state {current}: {exc}")
    current.write_text(
        json.dumps(merge(left, right), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
