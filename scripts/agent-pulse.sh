#!/usr/bin/env bash
# agent-pulse.sh — short "where am I" for agents (not a second box-status).
# Human-readable lines; optional --json for scripting.
# Does NOT auto-inject into sessions — run on demand when disoriented.
#
#   agent-pulse.sh
#   agent-pulse.sh --json
set -euo pipefail

JSON=0
[[ "${1:-}" == "--json" ]] && JSON=1

ASTRA="${ASTRA_REPO:-/root/astra-config}"
GROK_MCP="${GROK_MCP_REPO:-/root/grok-mcp}"
ALERT="${RESTART_ALERT:-/root/.grok-mcp-restart.alert}"
PUSH_FAIL="${ASTRA_PUSH_FAIL:-/root/.astra-push.failed}"

mcp_active="$(systemctl is-active grok-mcp.service 2>/dev/null || echo unknown)"
restart_alert=0
[[ -f "$ALERT" ]] && restart_alert=1
push_fail=0
[[ -f "$PUSH_FAIL" ]] && push_fail=1

astra_changes=0
if [[ -d "$ASTRA/.git" ]]; then
  astra_changes="$(git -C "$ASTRA" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
fi
grok_mcp_changes=0
if [[ -d "$GROK_MCP/.git" ]]; then
  grok_mcp_changes="$(git -C "$GROK_MCP" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
fi

# Harvest should stay off
harvest="off"
if systemctl --user is-enabled memory-harvest.timer &>/dev/null; then
  harvest="ENABLED (unexpected — should be off)"
elif systemctl --user is-active memory-harvest.timer &>/dev/null; then
  harvest="active (unexpected)"
fi

open_todos=0
TODOS="${HOME}/.local/state/astra/standing-todos.json"
if [[ -f "$TODOS" ]]; then
  open_todos="$(python3 -c "
import json
from pathlib import Path
d=json.loads(Path('$TODOS').read_text())
print(sum(1 for i in d.get('items',[]) if i.get('status')=='open'))
" 2>/dev/null || echo 0)"
fi

if [[ "$JSON" == 1 ]]; then
  python3 - <<PY
import json
print(json.dumps({
  "schema": "agent-pulse/v1",
  "grok_mcp_service": "$mcp_active",
  "restart_alert": bool($restart_alert),
  "astra_config_uncommitted_files": int("$astra_changes"),
  "grok_mcp_uncommitted_files": int("$grok_mcp_changes"),
  "astra_nightly_push_failed": bool($push_fail),
  "memory_harvest": "$harvest",
  "standing_todos_open": int("$open_todos"),
}, indent=2))
PY
  exit 0
fi

echo "=== agent pulse (where am I) ==="
echo "grok-mcp service:     $mcp_active"
if [[ "$restart_alert" == 1 ]]; then
  echo "restart alert:        YES — check claude.ai + Grok connectors (rm $ALERT when done)"
else
  echo "restart alert:        no"
fi
echo "astra-config:         $astra_changes file(s) with uncommitted edits (nightly commit/push backs this up)"
echo "grok-mcp repo:        $grok_mcp_changes file(s) with uncommitted edits (manual push only — not auto-backed-up)"
if [[ "$push_fail" == 1 ]]; then
  echo "astra nightly push:   FAILED — off-box backup may be stale"
else
  echo "astra nightly push:   ok (or nothing pending)"
fi
echo "memory harvest:       $harvest"
echo "standing todos open:  $open_todos"
echo "full box facts:       bash $ASTRA/scripts/box-status.sh --print"
echo "=== end pulse ==="
