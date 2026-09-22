"""Fail-fast project audit for NMT Brain deployments."""
import json
import re
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent
REQUIRED = [
    "nmt_brain.py",
    "nmt_strategy.py",
    "nmt_rules.json",
    "test_nmt_brain.py",
    "test_nmt_strategy.py",
    ".github/workflows/nmt-brain.yml",
]
OBSOLETE_AFTER_MIGRATION = [
    ".github/workflows/retire-old-brain.yml",
    ".github/workflows/send-brain-menu.yml",
    ".github/workflows/power-blocks-reminder.yml",
    ".github/workflows/nmt-brain-probe.yml",
]
SECRET_PATTERNS = [
    re.compile(r"TELEGRAM_BOT_TOKEN\s*=\s*['\"][0-9]{6,}:[A-Za-z0-9_-]{20,}['\"]"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
]


def fail(message):
    raise SystemExit("[AUDIT FAIL] " + message)


def main():
    for rel in REQUIRED:
        if not (ROOT / rel).exists():
            fail(f"missing required file: {rel}")

    rules = json.loads((ROOT / "nmt_rules.json").read_text(encoding="utf-8"))
    if rules.get("schema_version") != 1:
        fail("unexpected rules schema")
    verified = datetime.strptime(rules["verified_at"], "%Y-%m-%d").date()
    age = (date.today() - verified).days
    if age < 0 or age > 45:
        fail(f"mechanics snapshot is stale or invalid: age={age} days")

    pb = rules["power_blocks"]
    expected = {
        "board_cells": 10000,
        "winning_cells_min": 100,
        "winning_cells_max": 190,
        "nmt_per_hit": 15,
        "start_slots": 5,
        "max_slots": 19,
    }
    for key, value in expected.items():
        if pb.get(key) != value:
            fail(f"unexpected verified mechanic {key}={pb.get(key)!r}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix not in {".py", ".yml", ".yaml", ".json", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                fail(f"possible hardcoded secret in {path.relative_to(ROOT)}")

    brain = (ROOT / "nmt_brain.py").read_text(encoding="utf-8")
    required_commands = [
        "/pbset", "/profile", "/streak", "/nftcheck", "/marketmin",
        "/flip", "/mergecalc", "/collectionroi", "/plan", "/health",
    ]
    for command in required_commands:
        if command not in brain:
            fail(f"Brain command missing: {command}")

    workflow = (ROOT / ".github/workflows/nmt-brain.yml").read_text(encoding="utf-8")
    if "test_nmt_strategy.py" not in workflow or "audit_project.py" not in workflow:
        fail("CI does not enforce strategy tests + self-audit")

    remaining = [p for p in OBSOLETE_AFTER_MIGRATION if (ROOT / p).exists()]
    if remaining:
        fail("obsolete workflow(s) still present: " + ", ".join(remaining))

    print("[AUDIT OK] NMT Brain project checks passed")


if __name__ == "__main__":
    main()
