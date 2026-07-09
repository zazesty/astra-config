#!/usr/bin/env bash
# =============================================================================
# sync-grok-build-astra-mcp.sh — point Grok Build at local grok-mcp (loopback).
#
# Writes/updates [mcp_servers.astra] in ~/.grok/config.toml with:
#   url = http://127.0.0.1:3000${MCP_PATH}
# MCP_PATH is read from /etc/grok-mcp.env (secret; never printed fully).
#
# Full tool surface (memory_* + ask_oracle + research_fanout + …).
# Re-run after every MCP_PATH rotation (rotate-url.sh calls this automatically).
#
# Usage: sudo bash scripts/sync-grok-build-astra-mcp.sh
#        (or as root without sudo)
# =============================================================================
set -euo pipefail

ENV_FILE="${GROK_ENV:-/etc/grok-mcp.env}"
CONFIG="${GROK_CONFIG:-/root/.grok/config.toml}"
PORT="${GROK_MCP_PORT:-3000}"
NAME="astra"

[ -r "$ENV_FILE" ] || { echo "sync-grok-build-astra-mcp: cannot read $ENV_FILE" >&2; exit 1; }

MCP_PATH="$(grep -E '^MCP_PATH=' "$ENV_FILE" | head -n1 | cut -d= -f2- | cut -d, -f1 | tr -d '"' | tr -d "'")"
[ -n "$MCP_PATH" ] || { echo "sync-grok-build-astra-mcp: MCP_PATH empty in $ENV_FILE" >&2; exit 1; }
case "$MCP_PATH" in
  /*) ;;
  *) MCP_PATH="/$MCP_PATH" ;;
esac

URL="http://127.0.0.1:${PORT}${MCP_PATH}"

mkdir -p "$(dirname "$CONFIG")"
[ -f "$CONFIG" ] || printf '# Grok Build user config\n' >"$CONFIG"

python3 - "$CONFIG" "$NAME" "$URL" <<'PY'
import sys, re
from pathlib import Path

config_path, name, url = sys.argv[1], sys.argv[2], sys.argv[3]
text = Path(config_path).read_text()

block = f'''
# Auto-synced by astra-config/scripts/sync-grok-build-astra-mcp.sh (do not hand-edit URL;
# re-run that script after rotate-url.sh). Loopback full grok-mcp tool surface.
[mcp_servers.{name}]
url = "{url}"
enabled = true
startup_timeout_sec = 15
tool_timeout_sec = 120
'''

# Remove existing [mcp_servers.NAME] section (until next [section] or EOF)
pat = re.compile(
    rf"(?ms)^# Auto-synced by astra-config/scripts/sync-grok-build-astra-mcp\.sh.*?\n"
    rf"\[mcp_servers\.{re.escape(name)}\]\n(?:.*?\n)*?(?=^\[|\Z)"
)
text2, n = pat.subn("", text)
if n == 0:
    # Fallback: bare section without comment banner
    pat2 = re.compile(
        rf"(?ms)^\[mcp_servers\.{re.escape(name)}\]\n(?:.*?\n)*?(?=^\[|\Z)"
    )
    text2, n = pat2.subn("", text)

text2 = text2.rstrip() + "\n" + block
Path(config_path).write_text(text2)
print(f"sync-grok-build-astra-mcp: wrote [mcp_servers.{name}] -> loopback (path redacted)")
print(f"  config={config_path}")
print(f"  url=http://127.0.0.1:{url.split(':')[-1].split('/')[0] if False else '…'}")
# Safer: never print path
print(f"  host=127.0.0.1 port={url.split('/')[2].split(':')[-1] if '://' in url else '?'}")
PY

# Quick loopback probe (tools/list)
if curl -sS --max-time 8 -o /tmp/astra-mcp-probe.out -w '' -X POST "$URL" \
     -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'; then
  if grep -q 'research_fanout\|memory_search\|ask_oracle' /tmp/astra-mcp-probe.out 2>/dev/null; then
    count="$(grep -oE '"name":"[^"]+"' /tmp/astra-mcp-probe.out 2>/dev/null | wc -l | tr -d ' ')"
    echo "sync-grok-build-astra-mcp: loopback tools/list OK (name-hits≈${count})"
  else
    echo "sync-grok-build-astra-mcp: WARN loopback responded but tool names not found in body" >&2
  fi
else
  echo "sync-grok-build-astra-mcp: WARN loopback probe failed — is grok-mcp.service up?" >&2
fi

echo "sync-grok-build-astra-mcp: restart Grok Build session (or /mcp reload) to pick up tools."
