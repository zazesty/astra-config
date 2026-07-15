#!/usr/bin/env bash
# ops-log job body — machine-readable snapshot for co-admin parsing.
# Writes one JSON object per run to ~/.local/state/ops-log/ops-log.jsonl
# and a rolling latest pointer ops-log.latest.json
set -euo pipefail

OUT_DIR="${HOME}/.local/state/ops-log"
mkdir -p "$OUT_DIR"
JSONL="$OUT_DIR/ops-log.jsonl"
LATEST="$OUT_DIR/ops-log.latest.json"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PT=$(TZ=America/Los_Angeles date +%Y-%m-%dT%H:%M:%S%z)

export TS PT JSONL LATEST
python3 <<'PY'
import json, os, subprocess, glob
from pathlib import Path

ts = os.environ["TS"]
pt = os.environ["PT"]
jsonl = os.environ["JSONL"]
latest = os.environ["LATEST"]

def sh(cmd, timeout=20):
    try:
        p = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "rc": p.returncode,
            "out": (p.stdout or "")[-4000:],
            "err": (p.stderr or "")[-1000:],
        }
    except Exception as e:
        return {"rc": -1, "out": "", "err": str(e)}

def git_state(path):
    root = Path(path)
    if not (root / ".git").exists():
        return {"path": path, "exists": False}
    r = {"path": path, "exists": True}
    cmds = {
        "head": f"git -C {path} rev-parse --short HEAD 2>/dev/null",
        "branch": f"git -C {path} rev-parse --abbrev-ref HEAD 2>/dev/null",
        "dirty_n": f"git -C {path} status --porcelain 2>/dev/null | wc -l",
        "ahead": f"git -C {path} rev-list --count @{{u}}..HEAD 2>/dev/null || echo na",
        "behind": f"git -C {path} rev-list --count HEAD..@{{u}} 2>/dev/null || echo na",
    }
    for key, cmd in cmds.items():
        s = sh(cmd)
        r[key] = (s["out"] or "").strip()
    return r

def read_text(p, max_len=500):
    try:
        return Path(p).read_text()[:max_len]
    except Exception:
        return None

sentinels = {}
for p in [
    "/root/.grok-mcp-restart.alert",
    "/root/.astra-health.alerted",
    "/root/.astra-health.failcount",
    "/root/.config/grok-journal/paused",
    "/root/.config/grok-journal/enabled",
]:
    path = Path(p)
    sentinels[p] = {
        "exists": path.exists(),
        "content": read_text(p) if path.is_file() else None,
        "mtime": path.stat().st_mtime if path.exists() else None,
    }

timers = sh("systemctl --user list-timers --all --no-pager 2>/dev/null")
failed = sh("systemctl --user --failed --no-pager 2>/dev/null")
mcp = sh(
    "systemctl is-active grok-mcp.service 2>/dev/null; "
    "systemctl show grok-mcp.service -p ActiveState,SubState,MainPID,NRestarts --value 2>/dev/null | tr '\\n' ' '"
)
disk = sh("df -P / 2>/dev/null | tail -n +2")
mem = sh("free -m 2>/dev/null")
load = read_text("/proc/loadavg")
cron = sh("crontab -l 2>/dev/null | wc -l")

lasts = {}
for f in sorted(glob.glob("/root/.local/state/agent-jobs/*.last.json")):
    name = Path(f).name.replace(".last.json", "")
    try:
        lasts[name] = json.loads(Path(f).read_text())
    except Exception as e:
        lasts[name] = {"error": str(e)}

doc = {
    "schema": "ops-log/v1",
    "ts_utc": ts,
    "ts_pt": pt,
    "host": os.uname().nodename,
    "loadavg": load,
    "disk": disk,
    "memory": mem,
    "grok_mcp": mcp,
    "user_timers": timers,
    "user_failed_units": failed,
    "crontab_lines": cron,
    "git": {
        "astra-config": git_state("/root/astra-config"),
        "grok-mcp": git_state("/root/grok-mcp"),
        "Grok-Journal": git_state("/root/Grok-Journal"),
    },
    "sentinels": sentinels,
    "agent_job_last": lasts,
}

line = json.dumps(doc, separators=(",", ":"))
with open(jsonl, "a") as f:
    f.write(line + "\n")
Path(latest).write_text(json.dumps(doc, indent=2) + "\n")
print(f"ops-log wrote {latest}")
PY
