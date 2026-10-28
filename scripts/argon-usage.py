#!/usr/bin/env python3
"""Hourly snapshot of Grok Build subagent work versus this login.

Going forward only. The first run stores an epoch; turns that end before it
are ignored. Work is output tokens plus reasoning tokens. A parent turn
rolls child tokens into its own modelUsage, and the child session records
them again. subagent_ratio uses the parent rollup once: on a root session
that has children, non-Grok models are the children and Grok is this login.
A root session with no children stays on the login side, including a one-off
`grok -m flash`. A Grok-model child is moved out of that login total. The
meta.json body is never read. 1.0 means the children matched this login.
Flash and Argon are the split under that. work_ratio stays the older sum of
every usage file, Argon model work divided by Grok model work, so older rows
keep that meaning. call_ratio was dropped 2026-10-27. Older jsonl rows
still have it. Call counts stay on each side and off the status line.

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
GOAL = (
    "subagent_ratio 1.0 means child sessions did as much work as this login. "
    "work_ratio stays Argon model work divided by Grok model work."
)


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
    if "flash" in m:
        return "flash"
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


def child_ids() -> tuple[set[str], set[str]]:
    """Child id, then parent id. The meta.json body can hold a prompt."""
    children: set[str] = set()
    parents: set[str] = set()
    for path in SESSIONS.glob("**/subagents/*/meta.json"):
        children.add(path.parent.name)
        parents.add(path.parents[2].name)
    return children, parents


def consume(side: dict, sid: str, model: str, row: dict, seen: set) -> None:
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


def move_grok_children(login: dict, child_grok: dict) -> None:
    """Parent Grok totals include a Grok-model child. Move that piece once."""
    if child_grok["work"] <= 0:
        return
    take_work = min(login["work"], child_grok["work"])
    if take_work <= 0:
        return
    scale = take_work / child_grok["work"]
    for key in ("calls", "out", "reason", "work"):
        login[key] = max(0, login[key] - int(round(child_grok[key] * scale)))
    for model, calls in child_grok["models"].items():
        left = login["models"].get(model, 0) - int(round(calls * scale))
        if left > 0:
            login["models"][model] = left
        else:
            login["models"].pop(model, None)


def scan(epoch: datetime) -> dict:
    children, parents = child_ids()
    model = {name: blank() for name in ("grok", "argon", "flash", "other")}
    login = blank()
    sub = {name: blank() for name in ("all", "grok", "argon", "flash", "other")}
    for path in SESSIONS.glob("**/usage.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        sid = data.get("sessionId") or path.parent.name
        is_child = sid in children
        is_parent = sid in parents
        for turn in data.get("turns") or []:
            ended = parse_ts(turn.get("endedAt") or "")
            if ended is None or ended < epoch:
                continue
            usage = turn.get("modelUsage") or {}
            if not usage and turn.get("primaryModelId"):
                usage = {turn["primaryModelId"]: turn}
            seen_model: dict[str, set] = {}
            seen_login: set = set()
            seen_all: set = set()
            seen_seat: dict[str, set] = {}
            for name, row in usage.items():
                seat = bucket(name)
                seen_model.setdefault(seat, set())
                consume(model[seat], sid, name, row, seen_model[seat])
                # Child files repeat the parent rollup. Keep a Grok child so
                # it can leave the login side. Flash and Argon come from the rollup.
                if is_child:
                    if seat == "grok":
                        consume(sub["all"], sid, name, row, seen_all)
                        seen_seat.setdefault(seat, set())
                        consume(sub[seat], sid, name, row, seen_seat[seat])
                    continue
                if is_parent and seat != "grok":
                    consume(sub["all"], sid, name, row, seen_all)
                    seen_seat.setdefault(seat, set())
                    consume(sub[seat], sid, name, row, seen_seat[seat])
                    continue
                consume(login, sid, name, row, seen_login)
    move_grok_children(login, sub["grok"])
    return {"model": model, "login": login, "subagent": sub}


def ratio(num: int, den: int):
    if den <= 0:
        return None
    return round(num / den, 4)


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


def snapshot(now: datetime, epoch: datetime, scanned: dict) -> dict:
    model = scanned["model"]
    sub = scanned["subagent"]
    sub_pack = pack(sub["all"])
    sub_pack["flash"] = pack(sub["flash"])
    sub_pack["argon"] = pack(sub["argon"])
    sub_pack["grok"] = pack(sub["grok"])
    sub_pack["other"] = pack(sub["other"])
    return {
        "ts": now.isoformat(),
        "since": epoch.isoformat(),
        "grok": pack(model["grok"]),
        "argon": pack(model["argon"]),
        "flash": pack(model["flash"]),
        "other": pack(model["other"]),
        "work_ratio": ratio(model["argon"]["work"], model["grok"]["work"]),
        "login": pack(scanned["login"]),
        "subagent": sub_pack,
        "subagent_ratio": ratio(sub["all"]["work"], scanned["login"]["work"]),
        "goal": GOAL,
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


def fmt_ratio(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def line(row: dict) -> str:
    sub = row["subagent"]
    return (
        f"argon-usage: since {row['since']} "
        f"login_work={row['login']['work_tokens']} "
        f"subagent_work={sub['work_tokens']} "
        f"flash_work={sub['flash']['work_tokens']} "
        f"argon_work={sub['argon']['work_tokens']} "
        f"subagent_ratio={fmt_ratio(row['subagent_ratio'])}"
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

        def usage_row(calls, out, reason, cost=None):
            row = {
                "modelCalls": calls,
                "outputTokens": out,
                "reasoningTokens": reason,
                "inputTokens": 10,
            }
            if cost is not None:
                row["costUsdTicks"] = int(cost * 1e10)
            return row

        def turn(ended, model, calls, out, reason, cost=None):
            return {
                "endedAt": ended,
                "primaryModelId": model,
                "modelUsage": {model: usage_row(calls, out, reason, cost)},
            }

        def write_usage(sid: str, turns: list) -> None:
            folder = sessions / sid
            folder.mkdir(parents=True)
            (folder / "usage.json").write_text(json.dumps({
                "sessionId": sid,
                "turns": turns,
            }))

        def mark_child(parent: str, sid: str) -> None:
            folder = sessions / parent / "subagents" / sid
            folder.mkdir(parents=True)
            # Body is a prompt-shaped trap. The scanner must not need it.
            (folder / "meta.json").write_text('{"prompt":"secret-prompt-do-not-count"}\n')

        # Parent rollup includes each child's tokens. Child files repeat them.
        write_usage("g1", [
            turn("2026-10-13T21:00:00+00:00", "grok-4.7-build", 10, 100, 50, 1.5),
            {
                "endedAt": "2026-10-13T23:00:00+00:00",
                "primaryModelId": "gemini-4-argon",
                "modelUsage": {
                    "grok-4.7-build": usage_row(4, 48, 12, 0.5),
                    "gemini-4-argon": usage_row(4, 25, 25),
                    "gemini-3.8-flash": usage_row(2, 10, 5),
                },
            },
        ])
        write_usage("f-root", [
            turn("2026-10-13T23:10:00+00:00", "gemini-3.8-flash", 1, 4, 1),
        ])
        write_usage("a1", [
            turn("2026-10-13T23:30:00+00:00", "gemini-4-argon", 4, 25, 25),
        ])
        write_usage("f1", [
            turn("2026-10-13T23:40:00+00:00", "gemini-3.8-flash", 2, 10, 5),
        ])
        write_usage("g2", [
            turn("2026-10-13T23:45:00+00:00", "grok-4.7-build", 1, 8, 2),
        ])
        write_usage("o1", [
            turn("2026-10-13T23:50:00+00:00", "gemini-4-argon", 1, 7, 0),
        ])
        mark_child("g1", "a1")
        mark_child("g1", "f1")
        mark_child("g1", "g2")

        row = snapshot(epoch, epoch, scan(epoch))
        check("pre-epoch grok dropped", row["grok"]["calls"] == 5)
        check("argon model calls", row["argon"]["calls"] == 9)
        check("call ratio archived", "call_ratio" not in row)
        check("model work keeps the double count", row["grok"]["work_tokens"] == 70 and row["argon"]["work_tokens"] == 107)
        check("model flash keeps the double count", row["flash"]["work_tokens"] == 35)
        check("work ratio stays argon over grok", row["work_ratio"] == 1.5286)
        check("login is parent grok plus one-shots", row["login"]["work_tokens"] == 62)
        check("subagent uses the rollup once", row["subagent"]["work_tokens"] == 75)
        check("subagent flash is the rollup", row["subagent"]["flash"]["work_tokens"] == 15)
        check("subagent argon excludes the orphan", row["subagent"]["argon"]["work_tokens"] == 50)
        check("grok child leaves the login", row["subagent"]["grok"]["work_tokens"] == 10)
        check("subagent ratio", row["subagent_ratio"] == 1.2097)
        check("argon cost absent", row["argon"]["cost_usd"] is None)
        check("grok cost", row["grok"]["cost_usd"] == 0.5)
        check("zero denominator", ratio(3, 0) is None)
        check("flash bucket", bucket("gemini-3.8-flash") == "flash" and bucket("gemini-flash-latest") == "flash")
        check("argon bucket", bucket("gemini-4-argon") == "argon")
        check("grok bucket", bucket("grok-4.7-build") == "grok")
        check("prompt not in snapshot", "secret-prompt" not in json.dumps(row))
        write_snapshot(row)
        written = json.loads(LATEST_PATH.read_text())
        check("latest written", written["work_ratio"] == 1.5286 and written["subagent_ratio"] == 1.2097)
        check("phone not in line", "153" not in line(row))
        text = line(row)
        check(
            "status line is subagent work",
            "call_ratio" not in text
            and "work_ratio" not in text
            and "subagent_ratio=1.210" in text
            and "flash_work=15" in text
            and "argon_work=50" in text,
        )

    print(f"argon-usage self-test: {'pass' if ok else 'fail'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if len(sys.argv) > 1:
        print("argon-usage: unknown arg", file=sys.stderr)
        sys.exit(2)
    sys.exit(live())
