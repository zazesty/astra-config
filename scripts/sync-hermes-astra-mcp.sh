#!/usr/bin/env bash
# sync-hermes-astra-mcp.sh — point Hermes Agent at local grok-mcp (loopback).
# Re-run after every MCP_PATH rotation. Never prints full path.
set -euo pipefail
ENV_FILE="${GROK_ENV:-/etc/grok-mcp.env}"
PORT="${GROK_MCP_PORT:-3000}"
CFG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"
[ -r "$ENV_FILE" ] || { echo "cannot read $ENV_FILE" >&2; exit 1; }
MCP_PATH="$(grep -E '^MCP_PATH=' "$ENV_FILE" | head -n1 | cut -d= -f2- | cut -d, -f1 | tr -d '"' | tr -d "'")"
[ -n "$MCP_PATH" ] || { echo "MCP_PATH empty" >&2; exit 1; }
case "$MCP_PATH" in /*) ;; *) MCP_PATH="/$MCP_PATH" ;; esac
URL="http://127.0.0.1:${PORT}${MCP_PATH}"
python3 - "$CFG" "$URL" <<'PY'
import sys, yaml
from pathlib import Path
cfg_path, url = Path(sys.argv[1]), sys.argv[2]
c = yaml.safe_load(cfg_path.read_text()) if cfg_path.is_file() else {}
c = c or {}
c.setdefault("mcp_servers", {})["astra"] = {
    "url": url,
    "timeout": 180,
    "connect_timeout": 30,
}
cfg_path.parent.mkdir(parents=True, exist_ok=True)
cfg_path.write_text(yaml.dump(c, default_flow_style=False, sort_keys=False))
cfg_path.chmod(0o600)
print("sync-hermes-astra-mcp: astra URL updated (loopback)")
PY
