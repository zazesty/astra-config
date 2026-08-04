#!/usr/bin/env bash
# box-status.sh — regenerate machine-readable box pulse from LIVE sources.
# No MCP tool (no rotation tax). Do not hand-edit the output JSON.
#
#   box-status.sh           # write JSON, print path
#   box-status.sh --print   # write + pretty JSON on stdout
#   box-status.sh --stdout  # write + compact JSON on stdout
set -euo pipefail
export BOX_STATUS_OUT="${BOX_STATUS_OUT:-$HOME/.local/state/astra/box-status.json}"
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export AGENT_REPO="${AGENT_REPO:-/root/astra-config}"
exec python3 - "$@" <<'PY'
import json, os, shutil, socket, subprocess, sys
from datetime import date, datetime, timezone
from pathlib import Path

out_path = Path(os.environ["BOX_STATUS_OUT"])
state = out_path.parent
state.mkdir(parents=True, exist_ok=True)
repo = os.environ.get("AGENT_REPO", "/root/astra-config")
hermes_state = Path(os.environ["HERMES_FINANCE_STATE"])
agent_state = Path.home() / ".local/state/agent-jobs"
todos_path = state / "standing-todos.json"
env_script = state / "env-presence.sh"
ops_latest = Path.home() / ".local/state/ops-log/ops-log.latest.json"


def loadj(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


load = list(os.getloadavg())
du = shutil.disk_usage("/")
mem = {}
with open("/proc/meminfo") as f:
    for line in f:
        k, v = line.split(":")
        mem[k] = int(v.strip().split()[0])
avail, total = mem.get("MemAvailable", 0), mem.get("MemTotal", 1)

try:
    grok = subprocess.check_output(
        ["systemctl", "is-active", "grok-mcp.service"], text=True
    ).strip()
except Exception:
    grok = "unknown"

hermes = {
    "state_dir": str(hermes_state),
    "config_present": (hermes_state / "config.json").is_file(),
    "mode": None,
    "hardcap_cents": None,
    "notify_enabled": None,
    "bills_count": 0,
    "goals_count": 0,
    "spend_to_date_cents": None,
    "remaining_cents": None,
    "pct": None,
    "risk": None,
    "txn_count": None,
    "last_run": loadj(hermes_state / "last_run.json"),
    "as_of": date.today().isoformat(),
}
cfg = loadj(hermes_state / "config.json")
if cfg:
    hermes["mode"] = cfg.get("mode")
    hermes["hardcap_cents"] = cfg.get("hardcap_cents")
    hermes["notify_enabled"] = cfg.get("notify_enabled")
    hermes["bills_count"] = len(cfg.get("bills") or [])
    hermes["goals_count"] = len(cfg.get("goals") or [])
    hardcap = int(cfg.get("hardcap_cents") or 0)
    txns_path = hermes_state / "txns.json"
    if txns_path.is_file():
        raw = loadj(txns_path) or []
        txns = (
            raw
            if isinstance(raw, list)
            else (raw.get("transactions") or raw.get("txns") or [])
        )
        hermes["txn_count"] = len(txns)
        today = date.today()
        start = date(today.year, today.month, 1)
        spend = 0
        for t in txns:
            try:
                d = date.fromisoformat(str(t.get("date", ""))[:10])
            except Exception:
                continue
            if d < start or d > today:
                continue
            if t.get("transfer") or t.get("excluded") or t.get("pending"):
                continue
            amt = int(t.get("amount_cents") or 0)
            if amt > 0:
                spend += amt
        hermes["spend_to_date_cents"] = spend
        hermes["remaining_cents"] = hardcap - spend if hardcap else None
        hermes["pct"] = round(spend / hardcap, 4) if hardcap else None
        if hardcap and spend >= hardcap:
            hermes["risk"] = "breach"
        elif hardcap and spend / hardcap >= float(cfg.get("hardcap_warn_pct") or 0.8):
            hermes["risk"] = "warn"
        else:
            hermes["risk"] = "ok"

jobs = {}
if agent_state.is_dir():
    for p in sorted(agent_state.glob("*.last.json")):
        jobs[p.name.replace(".last.json", "")] = loadj(p)

todos = loadj(todos_path) or {"items": [], "updated": None}
open_items = [i for i in todos.get("items", []) if i.get("status") == "open"]

env = {"keys": {}, "files": {}}
if env_script.is_file():
    try:
        out = subprocess.check_output(
            ["bash", str(env_script)], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "FILE":
                env["files"][parts[0]] = parts[2]
            elif len(parts) >= 3:
                env["keys"][parts[1]] = parts[2]
    except Exception as e:
        env["error"] = str(e)

generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
doc = {
    "schema": "box-status/v1",
    "generated_at": generated,
    "hostname": socket.gethostname(),
    "freshness": {
        "note": "Regenerated each run from live files/services. Re-run before trusting; do not hand-edit.",
        "generated_at": generated,
    },
    "services": {
        "grok_mcp": grok,
        "restart_alert_present": Path("/root/.grok-mcp-restart.alert").is_file(),
    },
    "resources": {
        "loadavg": load,
        "disk_used_pct": round(100 * du.used / du.total, 1),
        "disk_free_bytes": du.free,
        "mem_available_kb": avail,
        "mem_used_pct": round(100 * (1 - avail / total), 1) if total else None,
    },
    "hermes": hermes,
    "agent_jobs": jobs,
    "standing_todos": {
        "open_count": len(open_items),
        "items": open_items[:20],
        "updated": todos.get("updated"),
    },
    "env_presence": env,
    "ops_log_latest_present": ops_latest.is_file(),
    "pointers": {
        "operator_md": "/root/OPERATOR.md",
        "standing_todos": str(todos_path),
        "env_map": str(state / "env-map.md"),
        "hermes_state": str(hermes_state),
        "this_file": str(out_path),
        "script": f"{repo}/scripts/box-status.sh",
    },
}
out_path.write_text(json.dumps(doc, indent=2) + "\n")
print(str(out_path))
if "--print" in sys.argv:
    print(json.dumps(doc, indent=2))
elif "--stdout" in sys.argv:
    print(json.dumps(doc))
PY
