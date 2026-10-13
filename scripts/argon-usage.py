#!/usr/bin/env python3
"""Hourly snapshot of Grok Build Argon use versus this login.

Going forward only. The first run stores an epoch; turns that end before it
are ignored. Work is output tokens plus reasoning tokens. call_ratio and
work_ratio are Argon divided by Grok. 1.0 means the two sides did the same
amount, which is about half the measured work on Argon and about double the
subscription side.

Writes ~/.local/state/astra/argon-usage.json (latest) and .jsonl (history).
No email, no Pushover. Session text is never read.

  argon-usage.py --self-test
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SESSIONS = Path(os.environ.get("ARGON_USAGE_SESSIONS", "/root/.grok/sessions"))
STATE_DIR = Path(os.environ.get("ARGON_USAGE_STATE", "/root/.local/state/astra"))
EPOCH_PATH = STATE_DIR / "argon-usage-epoch"
LATEST_PATH = STATE_DIR / "argon-usage.json"
LOG_PATH = STATE_DIR / "argon-usage.jsonl"


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def bucket(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("grok"):
        return "grok"
    if "argon" in m or "gemini" in m:
        return "argon"
    return "other"


def ticks(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value) / 1e10
    except (TypeError, ValueError):
        return None


def load_epoch(now: datetime) -> datetime:
    if EPOCH_PATH.is_file():
        saved = parse_ts(EPOCH_PATH.read_text().strip())
        if saved is not None:
            return saved
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    EPOCH_PATH.write_text(now.isoformat() + "\n")
    os.chmod(EPOCH_PATH, 0o600)
    return now


def blank():
    return {
        "calls": 0,
        "out": 0,
        "reason": 0,
        "work": 0,
        "cost": 0.0,
        "cost_known": False,
        "turns": 0,
        "sessions": set(),
        "models": defaultdict(int),
    }


def scan(epoch: datetime) -> dict:
    sides = {"grok": blank(), "argon": blank(), "other": blank()}
    for path in SESSIONS.glob("**/usage.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        sid = data.get("sessionId") or path.parent.name
        for turn in data.get("turns") or []:
            ended = parse_ts(turn.get("endedAt") or "")
            if ended is None or ended < epoch:
                continue
            usage = turn.get("modelUsage") or {}
            if not usage and turn.get("primaryModelId"):
                usage = {turn["primaryModelId"]: turn}
            seen = set()
            for model, row in usage.items():
                side = sides[bucket(model)]
                calls = int(row.get("modelCalls") or 0)
                out = int(row.get("outputTokens") or 0)
                reason = int(row.get("reasoningTokens") or 0)
                side["calls"] += calls
                side["out"] += out
                side["reason"] += reason
                side["work"] += out + reason
                cost = ticks(row.get("costUsdTicks"))
                if cost is not None:
                    side["cost"] += cost
                    side["cost_known"] = True
                side["models"][model] += calls
                if model not in seen:
                    side["turns"] += 1
                    seen.add(model)
                if calls or out or reason:
                    side["sessions"].add(sid)
    return sides


def ratio(num: int, den: int):
    if den <= 0:
        return None
    return round(num / den, 4)


def snapshot(now: datetime, epoch: datetime, sides: dict) -> dict:
    def pack(side: dict) -> dict:
        return {
            "calls": side["calls"],
            "output_tokens": side["out"],
            "reasoning_tokens": side["reason"],
            "work_tokens": side["work"],
            "cost_usd": round(side["cost"], 4) if side["cost_known"] else None,
            "turns": side["turns"],
            "sessions": len(side["sessions"]),
            "models": dict(sorted(side["models"].items())),
        }

    grok = sides["grok"]
    argon = sides["argon"]
    return {
        "ts": now.isoformat(),
        "since": epoch.isoformat(),
        "grok": pack(grok),
        "argon": pack(argon),
        "other": pack(sides["other"]),
        "call_ratio": ratio(argon["calls"], grok["calls"]),
        "work_ratio": ratio(argon["work"], grok["work"]),
        "goal": "work_ratio 1.0 means Argon did as much work as this login (about half delegated).",
    }


def write_snapshot(row: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, indent=2) + "\n"
    tmp = LATEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(payload)
    os.chmod(tmp, 0o600)
    tmp.replace(LATEST_PATH)
    with LOG_PATH.open("a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    os.chmod(LOG_PATH, 0o600)
    if LOG_PATH.stat().st_size > 2_000_000:
        lines = LOG_PATH.read_text().splitlines()[-2000:]
        LOG_PATH.write_text("\n".join(lines) + "\n")
        os.chmod(LOG_PATH, 0o600)


def line(row: dict) -> str:
    cr = row["call_ratio"]
    wr = row["work_ratio"]
    cr_s = "n/a" if cr is None else f"{cr:.3f}"
    wr_s = "n/a" if wr is None else f"{wr:.3f}"
    return (
        f"argon-usage: since {row['since']} "
        f"grok_calls={row['grok']['calls']} argon_calls={row['argon']['calls']} "
        f"call_ratio={cr_s} work_ratio={wr_s}"
    )


def live() -> int:
    now = datetime.now(timezone.utc)
    epoch = load_epoch(now)
    row = snapshot(now, epoch, scan(epoch))
    write_snapshot(row)
    print(line(row))
    return 0


def self_test() -> int:
    import tempfile

    global SESSIONS, STATE_DIR, EPOCH_PATH, LATEST_PATH, LOG_PATH
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        ok = ok and cond
        print(("ok  " if cond else "FAIL") + " " + name)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sessions = root / "sessions"
        state = root / "state"
        SESSIONS, STATE_DIR = sessions, state
        EPOCH_PATH = state / "argon-usage-epoch"
        LATEST_PATH = state / "argon-usage.json"
        LOG_PATH = state / "argon-usage.jsonl"
        epoch = datetime(2026, 10, 13, 22, 0, tzinfo=timezone.utc)
        state.mkdir()
        EPOCH_PATH.write_text(epoch.isoformat() + "\n")

        def turn(ended, model, calls, out, reason, cost=None):
            row = {
                "modelCalls": calls,
                "outputTokens": out,
                "reasoningTokens": reason,
                "inputTokens": 10,
            }
            if cost is not None:
                row["costUsdTicks"] = int(cost * 1e10)
            return {
                "endedAt": ended,
                "primaryModelId": model,
                "modelUsage": {model: row},
            }

        grok_dir = sessions / "s-grok"
        argon_dir = sessions / "s-argon"
        grok_dir.mkdir(parents=True)
        argon_dir.mkdir(parents=True)
        (grok_dir / "usage.json").write_text(json.dumps({
            "sessionId": "g1",
            "turns": [
                turn("2026-10-13T21:00:00+00:00", "grok-4.7-build", 10, 100, 50, 1.5),
                turn("2026-10-13T23:00:00+00:00", "grok-4.7-build", 4, 40, 10, 0.5),
            ],
        }))
        (argon_dir / "usage.json").write_text(json.dumps({
            "sessionId": "a1",
            "turns": [
                turn("2026-10-13T23:30:00+00:00", "gemini-4-argon", 4, 25, 25),
            ],
        }))
        row = snapshot(epoch, epoch, scan(epoch))
        check("pre-epoch grok dropped", row["grok"]["calls"] == 4)
        check("argon calls", row["argon"]["calls"] == 4)
        check("call ratio 1", row["call_ratio"] == 1.0)
        check("work tokens", row["grok"]["work_tokens"] == 50 and row["argon"]["work_tokens"] == 50)
        check("work ratio 1", row["work_ratio"] == 1.0)
        check("argon cost absent", row["argon"]["cost_usd"] is None)
        check("grok cost", row["grok"]["cost_usd"] == 0.5)
        check("zero denominator", ratio(3, 0) is None)
        check("old flash counts as the seat", bucket("gemini-flash-latest") == "argon")
        check("grok bucket", bucket("grok-4.7-build") == "grok")
        write_snapshot(row)
        check("latest written", json.loads(LATEST_PATH.read_text())["call_ratio"] == 1.0)
        check("phone not in line", "153" not in line(row))

    print(f"argon-usage self-test: {'pass' if ok else 'fail'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if len(sys.argv) > 1:
        print("argon-usage: unknown arg", file=sys.stderr)
        sys.exit(2)
    sys.exit(live())
