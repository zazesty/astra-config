#!/usr/bin/env python3
"""weekly-kaizen-preflight.py — $0 read-only gatherer for Hermes weekly kaizen.

Collects telemetry, alert sentinels, failed systemd units, recent service error
tails, 7d repo activity, open standing-todos, pinned OpenRouter model diffs,
and timer/script activity metadata (for LLM review; never auto-deletes).

Security/privacy: never reads /root/.local/state/budget-bot, /etc/*.env, or
raw conversation logs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
HOME = Path.home()
STATE_DIR = HOME / ".local/state/astra/kaizen-staging"
PIN_CACHE = STATE_DIR / "openrouter-pins.json"
PIN_PENDING = STATE_DIR / "openrouter-pins.pending.json"
TELEMETRY_MD = HOME / ".local/state/astra/vps-telemetry.md"
STANDING_TODOS_SH = Path("/root/astra-config/scripts/standing-todos.sh")

ALERT_FILES = (
    HOME / ".astra-drift.alert",
    HOME / ".astra-push.failed",
    HOME / ".grok-mcp-restart.alert",
    HOME / ".hermes-alert",
)

PINNED_MODELS = (
    "x-ai/grok-4.7",
    "google/gemini-4-neon",
    "google/gemini-4-hydrogen",
    "google/gemini-3.5-flash-lite",
    "~openai/gpt-sol-latest",
    "~anthropic/claude-sonnet-latest",
    "openrouter/auto-beta",
)

JOURNAL_UNITS = (
    "grok-mcp.service",
    "grok-argon-proxy.service",
    "hermes-gateway.service",
)


def _user_ctl_env() -> dict[str, str]:
    env = os.environ.copy()
    if os.geteuid() == 0 and Path("/run/user/0/bus").exists():
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/0")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/0/bus")
    return env


def _run(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = 15) -> str:
    try:
        res = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if res.returncode != 0 and not out:
            return f"(exit {res.returncode}: {err[:240]})"
        return out
    except Exception as exc:
        return f"(error: {exc})"


def section_telemetry() -> str:
    if not TELEMETRY_MD.is_file():
        return "(vps-telemetry.md missing)"
    lines = TELEMETRY_MD.read_text(errors="replace").splitlines()
    return "\n".join(lines[:30])


def section_alerts_and_units() -> str:
    out: list[str] = []
    for af in ALERT_FILES:
        if af.is_file():
            body = af.read_text(errors="replace").strip().splitlines()
            out.append(f"- ALERT `{af}`: " + " | ".join(body[:5]))
    sys_failed = _run(["systemctl", "--failed", "--no-legend", "--no-pager"])
    usr_failed = _run(
        ["systemctl", "--user", "--failed", "--no-legend", "--no-pager"],
        env=_user_ctl_env(),
    )
    out.append(f"- system failed units: {sys_failed or 'none'}")
    out.append(f"- user failed units: {usr_failed or 'none'}")
    return "\n".join(out)


def section_journal_errors() -> str:
    lines: list[str] = []
    for unit in JOURNAL_UNITS:
        raw = _run(
            [
                "journalctl",
                "-u",
                unit,
                "--since",
                "7 days ago",
                "-p",
                "err",
                "--no-pager",
                "-n",
                "8",
                "-o",
                "short-iso",
            ]
        )
        cleaned = [ln for ln in raw.splitlines() if ln and not ln.startswith("-- No entries")]
        lines.append(f"### {unit}")
        if not cleaned:
            lines.append("none")
        else:
            lines.extend(cleaned[:8])
    return "\n".join(lines)


def section_git_activity() -> str:
    parts: list[str] = []
    for repo in ("/root/astra-config", "/root/grok-mcp"):
        status = _run(["git", "-C", repo, "status", "--short"])
        log = _run(["git", "-C", repo, "log", "--since=7 days ago", "--oneline", "-n", "15"])
        parts.append(f"### {repo}")
        parts.append(f"status: {status or 'clean'}")
        parts.append("7d commits:\n" + (log or "(none)"))
    return "\n".join(parts)


def section_open_todos() -> str:
    if not STANDING_TODOS_SH.is_file():
        return "(standing-todos.sh missing)"
    return _run([str(STANDING_TODOS_SH), "list"])


def diff_openrouter_pins(
    live_models: dict[str, dict],
    prev_snapshot: dict[str, dict] | None,
) -> tuple[list[str], dict[str, dict]]:
    """Compute human-readable deltas for PINNED_MODELS and return the new snapshot."""
    current: dict[str, dict] = {}
    notes: list[str] = []
    for slug in PINNED_MODELS:
        info = live_models.get(slug)
        if not info:
            current[slug] = {"present": False}
            notes.append(f"- `{slug}`: MISSING from OpenRouter catalog")
            continue
        pricing = info.get("pricing") or {}
        rec = {
            "present": True,
            "prompt": str(pricing.get("prompt", "")),
            "completion": str(pricing.get("completion", "")),
            "input_cache_read": str(pricing.get("input_cache_read", "")),
            "context_length": info.get("context_length"),
        }
        current[slug] = rec
        if prev_snapshot is None:
            notes.append(
                f"- `{slug}` (baseline seeded): in=${_per_m(rec['prompt'])}/M, "
                f"cached=${_per_m(rec['input_cache_read'])}/M, out=${_per_m(rec['completion'])}/M"
            )
        else:
            old = prev_snapshot.get(slug)
            if old != rec:
                notes.append(f"- `{slug}` CHANGED: {old} -> {rec}")
    if prev_snapshot is not None and not notes:
        notes.append("No changes in pinned OpenRouter models/pricing vs prior snapshot.")
    return notes, current


def _per_m(raw: str) -> str:
    try:
        val = float(raw)
        if val < 0:
            return "dynamic"
        return f"{val * 1_000_000:.3g}"
    except Exception:
        return raw or "?"


def observe_openrouter_pins(
    live_models: dict[str, dict],
    pin_cache: Path = PIN_CACHE,
    pin_pending: Path = PIN_PENDING,
) -> list[str]:
    """Diff live_models against acknowledged pin_cache and write only to pin_pending."""
    prev: dict[str, dict] | None = None
    if pin_cache.is_file():
        try:
            prev = json.loads(pin_cache.read_text()).get("models")
        except Exception:
            prev = None

    notes, current = diff_openrouter_pins(live_models, prev)
    pin_pending.parent.mkdir(parents=True, exist_ok=True)
    if pin_pending.parent == STATE_DIR:
        os.chmod(STATE_DIR, 0o700)
    tmp = pin_pending.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"updated_at": datetime.now(timezone.utc).isoformat(), "models": current},
            indent=2,
        )
        + "\n"
    )
    tmp.replace(pin_pending)
    return notes


def ack_openrouter_pins(
    pin_cache: Path = PIN_CACHE,
    pin_pending: Path = PIN_PENDING,
) -> int:
    """Promote pending OpenRouter pin snapshot to acknowledged baseline."""
    if not pin_pending.is_file():
        print("ack-pins: no pending snapshot to acknowledge.")
        return 0
    pin_pending.replace(pin_cache)
    print(f"ack-pins: promoted pending snapshot to {pin_cache}")
    return 0


def section_openrouter_watch() -> str:
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "zaz-astra-weekly-kaizen/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        live_models = {m["id"]: m for m in payload.get("data", []) if isinstance(m, dict) and "id" in m}
    except Exception as exc:
        return f"(OpenRouter fetch failed: {exc})"

    notes = observe_openrouter_pins(live_models)
    return "\n".join(notes)


def find_stale_candidates(
    scripts_dirs: list[Path],
    caller_corpus: str,
    now_ts: float,
    min_age_days: int = 45,
) -> list[str]:
    """Identify aged script files unreferenced outside themselves."""
    script_bodies: dict[Path, str] = {}
    for directory in scripts_dirs:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix in (".sh", ".py"):
                script_bodies[entry] = entry.read_text(errors="replace")[:32000]

    candidates: list[str] = []
    cutoff = now_ts - (min_age_days * 86400)
    for entry, _body in sorted(script_bodies.items()):
        st = entry.stat()
        if st.st_mtime > cutoff:
            continue
        other_scripts = "\n".join(b for q, b in script_bodies.items() if q != entry)
        combined = caller_corpus + "\n" + other_scripts
        if entry.name not in combined and (len(entry.stem) < 4 or entry.stem not in combined):
            age_d = int((now_ts - st.st_mtime) / 86400)
            candidates.append(f"- `{entry}` (mtime {age_d}d ago, no direct ref in units/callers)")
    return candidates[:20]


def section_timers_and_cull_candidates() -> str:
    env = _user_ctl_env()
    timers_raw = _run(["systemctl", "--user", "list-timers", "--all", "--no-pager"], env=env)
    timer_lines = [ln for ln in timers_raw.splitlines() if ln.strip()][:25]

    caller_only: list[str] = []
    for base in (
        Path("/root/astra-config/home/.config/systemd/user"),
        Path("/root/astra-config/systemd"),
        Path("/root/astra-config/docs"),
        Path("/root/astra-config/setup.sh"),
        Path("/root/.hermes/cron"),
        Path("/root/grok-mcp/src"),
    ):
        if base.is_file():
            caller_only.append(base.read_text(errors="replace"))
        elif base.is_dir():
            for p in base.rglob("*"):
                if p.is_file() and p.suffix in (".service", ".timer", ".sh", ".py", ".json", ".md", ".txt", ".yaml", ".ts"):
                    try:
                        caller_only.append(p.read_text(errors="replace")[:32000])
                    except Exception:
                        pass

    now_ts = datetime.now(timezone.utc).timestamp()
    candidates = find_stale_candidates(
        [Path("/root/astra-config/scripts"), Path("/root/astra-config/agent-jobs")],
        "\n".join(caller_only),
        now_ts,
    )
    lines = ["### User Timers"] + timer_lines + ["", "### Aged Unreferenced Candidates (>45d, review only — NEVER auto-delete)"]
    lines.extend(candidates if candidates else ["none"])
    return "\n".join(lines)


def run_self_test() -> int:
    prev = {
        "x-ai/grok-4.7": {
            "present": True,
            "prompt": "0.000002",
            "completion": "0.000006",
            "input_cache_read": "0.0000002",
            "context_length": 2000000,
        }
    }
    live = {
        "x-ai/grok-4.7": {
            "pricing": {"prompt": "0.000003", "completion": "0.000006", "input_cache_read": "0.0000002"},
            "context_length": 2000000,
        }
    }
    notes, snap = diff_openrouter_pins(live, prev)
    assert any("`x-ai/grok-4.7` CHANGED" in n for n in notes), notes
    assert any("`google/gemini-4-neon`: MISSING" in n for n in notes), notes
    assert snap["google/gemini-4-neon"]["present"] is False

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        old = d / "legacy_unused.sh"
        old.write_text("#!/bin/sh\n")
        ancient = datetime.now(timezone.utc).timestamp() - (60 * 86400)
        os.utime(old, (ancient, ancient))
        fresh = d / "fresh.sh"
        fresh.write_text("#!/bin/sh\n")
        found = find_stale_candidates([d], "ExecStart=/somewhere/other.sh", datetime.now(timezone.utc).timestamp())
        assert len(found) == 1 and "legacy_unused.sh" in found[0], found

        # Verify OR pin deltas survive unacknowledged retries until --ack-pins
        cache = d / "pins.json"
        pending = d / "pins.pending.json"
        baseline = {slug: {"pricing": {"prompt": "0.000001", "completion": "0.000002", "input_cache_read": "0.0000001"}, "context_length": 1000} for slug in PINNED_MODELS}
        observe_openrouter_pins(baseline, cache, pending)
        ack_openrouter_pins(cache, pending)
        changed = {**baseline, "x-ai/grok-4.7": {"pricing": {"prompt": "0.000009", "completion": "0.000002", "input_cache_read": "0.0000001"}, "context_length": 1000}}
        first = observe_openrouter_pins(changed, cache, pending)
        retry = observe_openrouter_pins(changed, cache, pending)
        assert any("`x-ai/grok-4.7` CHANGED" in n for n in first), first
        assert retry == first, (first, retry)
        ack_openrouter_pins(cache, pending)
        after_ack = observe_openrouter_pins(changed, cache, pending)
        assert after_ack == ["No changes in pinned OpenRouter models/pricing vs prior snapshot."], after_ack
    print("weekly-kaizen-preflight self-test passed")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return run_self_test()
    if "--ack-pins" in sys.argv[1:]:
        return ack_openrouter_pins()
    now_pt = datetime.now(PT).strftime("%Y-%m-%d %H:%M %Z")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    sections = [
        f"# Weekly Kaizen Preflight ({now_pt})",
        f"Staging dir (writable for optional report only): `{STATE_DIR}`",
        "",
        "## 1. VPS Telemetry",
        section_telemetry(),
        "",
        "## 2. Alerts & Failed Units",
        section_alerts_and_units(),
        "",
        "## 3. Recent Service Errors (7d)",
        section_journal_errors(),
        "",
        "## 4. Repo Activity & Worktrees (7d)",
        section_git_activity(),
        "",
        "## 5. Open Standing Todos",
        section_open_todos(),
        "",
        "## 6. Pinned OpenRouter Model Watch",
        section_openrouter_watch(),
        "",
        "## 7. Timers & Cull Review Candidates",
        section_timers_and_cull_candidates(),
    ]
    print("\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
