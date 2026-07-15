#!/usr/bin/env bash
# consumer-health — irritation #3 fix.
# Detects silent consumer drift after MCP_PATH rotation / oauth death / dead /fire.
# Never prints secrets or full MCP_PATH.
#
# Checks:
#   A) funnel smoke (EXPECTED_TOOLS)
#   B) Grok Build loopback URL path matches /etc/grok-mcp.env MCP_PATH (hash compare)
#   C) journal-trigger endpoint+secret files present & non-empty
#   D) journal-cron.log: last successful fire age (warn if stale while pre-hard-stop)
#   E) optional: ~/.config/grok-journal/enabled vs paused state consistency
set -euo pipefail

REPO="${AGENT_REPO:-/root/astra-config}"
NOTIFY="${NOTIFY_CMD:-$REPO/scripts/notify-email.sh}"
ENV_FILE="${GROK_ENV:-/etc/grok-mcp.env}"
GROK_CFG="${GROK_CONFIG:-/root/.grok/config.toml}"
J_ENDPOINT="${HOME}/.config/journal-trigger/endpoint"
J_SECRET="${HOME}/.config/journal-trigger/secret"
J_LOG="${HOME}/.local/state/journal-cron.log"
REPORT="${HOME}/.local/state/agent-jobs/consumer-health.report.json"
# Claude journal hard-stop (after this, stale fires are expected)
STOP_EPOCH=$(TZ=America/Los_Angeles date -d "2026-07-19 21:00:00" +%s 2>/dev/null || echo 0)
NOW=$(date +%s)
# Warn if no successful journal fire in this many hours (while still in auto window)
STALE_H="${JOURNAL_FIRE_STALE_H:-36}"

mkdir -p "$(dirname "$REPORT")"
findings=()

# --- A: smoke ---
if [ -x "$REPO/scripts/smoke-test.sh" ]; then
  if ! out=$(RETRIES=1 SLEEP_SECS=2 bash "$REPO/scripts/smoke-test.sh" 2>&1); then
    findings+=("smoke: FAIL")
  fi
else
  findings+=("smoke: script missing")
fi

# --- B: Grok Build path match (hash only) ---
if [ -r "$ENV_FILE" ] && [ -f "$GROK_CFG" ]; then
  MCP_PATH=$(grep -E '^MCP_PATH=' "$ENV_FILE" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" | cut -d, -f1)
  case "$MCP_PATH" in /*) ;; *) MCP_PATH="/$MCP_PATH" ;; esac
  ENV_HASH=$(printf '%s' "$MCP_PATH" | sha256sum | awk '{print $1}')
  # extract url from [mcp_servers.astra]
  CFG_URL=$(python3 - "$GROK_CFG" <<'PY'
import sys, re
from pathlib import Path
t = Path(sys.argv[1]).read_text()
m = re.search(r'(?ms)\[mcp_servers\.astra\].*?^url\s*=\s*"([^"]+)"', t)
print(m.group(1) if m else "")
PY
)
  if [ -z "$CFG_URL" ]; then
    findings+=("grok_build: no [mcp_servers.astra] url in config")
  else
    # path component of URL
    CFG_PATH=$(python3 -c 'import sys,urllib.parse; u=urllib.parse.urlparse(sys.argv[1]); print(u.path or "")' "$CFG_URL")
    CFG_HASH=$(printf '%s' "$CFG_PATH" | sha256sum | awk '{print $1}')
    if [ "$ENV_HASH" != "$CFG_HASH" ]; then
      findings+=("grok_build: loopback MCP path hash != env MCP_PATH (run sync-grok-build-astra-mcp.sh)")
    fi
    # quick loopback tools/list without printing path
    if ! curl -sS --max-time 8 -o /tmp/consumer-health-mcp.out -X POST "$CFG_URL" \
         -H 'Content-Type: application/json' \
         -H 'Accept: application/json, text/event-stream' \
         -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 2>/dev/null; then
      findings+=("grok_build: loopback tools/list curl failed")
    elif ! grep -qE 'memory_search|ask_oracle|research_fanout' /tmp/consumer-health-mcp.out 2>/dev/null; then
      findings+=("grok_build: loopback tools/list missing expected tools")
    fi
  fi
else
  findings+=("grok_build: env or config unreadable")
fi

# --- C: journal trigger secrets present (not values) ---
if [ ! -s "$J_ENDPOINT" ]; then findings+=("journal_trigger: endpoint file missing/empty"); fi
if [ ! -s "$J_SECRET" ]; then findings+=("journal_trigger: secret file missing/empty"); fi

# --- D: last successful fire ---
LAST_FIRE_ISO=""
if [ -f "$J_LOG" ]; then
  LAST_FIRE_ISO=$(grep -E 'decision=fire.*post_http=(200|201)' "$J_LOG" | tail -1 | awk '{print $1}' || true)
fi
if [ -n "$LAST_FIRE_ISO" ]; then
  LAST_EPOCH=$(date -d "$LAST_FIRE_ISO" +%s 2>/dev/null || echo 0)
else
  LAST_EPOCH=0
fi
# Only warn about stale fires if still before hard-stop and cron still installed
CRON_HAS_JOURNAL=0
crontab -l 2>/dev/null | grep -q journal-trigger && CRON_HAS_JOURNAL=1 || true
if [ "$NOW" -lt "$STOP_EPOCH" ] && [ "$CRON_HAS_JOURNAL" = 1 ]; then
  if [ "$LAST_EPOCH" -eq 0 ]; then
    findings+=("journal_fire: no successful fire found in journal-cron.log")
  else
    age_h=$(( (NOW - LAST_EPOCH) / 3600 ))
    if [ "$age_h" -gt "$STALE_H" ]; then
      findings+=("journal_fire: last success ${age_h}h ago (threshold ${STALE_H}h) — may be silent fail")
    fi
  fi
fi

# --- E: grok journal pause flags (info only, not a finding) ---
GJ_ENABLED=0; GJ_PAUSED=0
[ -f "${HOME}/.config/grok-journal/enabled" ] && GJ_ENABLED=1
[ -f "${HOME}/.config/grok-journal/paused" ] && GJ_PAUSED=1

# Build report
export REPORT
FINDINGS_JSON=$(printf '%s\n' "${findings[@]+"${findings[@]}"}" | python3 -c 'import json,sys; print(json.dumps([ln.strip() for ln in sys.stdin if ln.strip()]))')
python3 - "$REPORT" "$FINDINGS_JSON" "${LAST_FIRE_ISO:-}" "$GJ_ENABLED" "$GJ_PAUSED" "$CRON_HAS_JOURNAL" <<'PY'
import json, sys
path, findings_s, last_fire, en, pa, cron = sys.argv[1:7]
findings = json.loads(findings_s)
doc = {
  "schema": "consumer-health/v1",
  "ok": len(findings) == 0,
  "findings": findings,
  "journal_last_fire_utc": last_fire or None,
  "grok_journal_enabled": en == "1",
  "grok_journal_paused_flag": pa == "1",
  "claude_journal_cron_present": cron == "1",
}
open(path, "w").write(json.dumps(doc, indent=2) + "\n")
print(json.dumps({"ok": doc["ok"], "n_findings": len(findings), "findings": findings}))
PY

if [ "${#findings[@]}" -gt 0 ]; then
  if [ -f "$NOTIFY" ]; then
    {
      echo "Consumer health findings (silent drift risk):"
      printf '  - %s\n' "${findings[@]}"
      echo
      echo "Report: $REPORT"
      echo "Human checklist: bash $REPO/scripts/post-rotate-checklist.sh"
      echo "Grok Build sync: bash $REPO/scripts/sync-grok-build-astra-mcp.sh"
      echo "Cloud connectors (journaling / claude.ai / Grok) still need manual re-add after rotation."
    } | bash "$NOTIFY" "🔴 astra consumer-health findings" || true
  fi
  exit 1
fi
exit 0
