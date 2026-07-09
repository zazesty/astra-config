#!/usr/bin/env bash
# =============================================================================
# restart-reminder.sh — debounced "grok-mcp restarted" in-session nudge.
#
# grok-mcp.service's ExecStartPost stamps $STATE_DIRECTORY/restart-marker (epoch)
# on every (re)start. This script runs on a short timer and fires ONCE after the
# restarts have SETTLED (no new restart within DEBOUNCE_SECS).
#
# Policy (2026-07-08): CHECK CONNECTORS AFTER EVERY RESTART; ROTATE AS NEEDED.
# Writes a sentinel for SessionStart / interactive shells — does NOT email for
# routine restarts (email reserved for truly broken health paths).
# =============================================================================
set -uo pipefail

MARKER="${RESTART_MARKER:-/var/lib/grok-mcp/restart-marker}"
SENT="${MARKER}.reminded"
ALERT="${GROK_RESTART_ALERT:-/root/.grok-mcp-restart.alert}"
DEBOUNCE="${DEBOUNCE_SECS:-600}"        # wait this long after the last restart

is_num() { case "$1" in ''|*[!0-9]*) return 1;; *) return 0;; esac; }

[ -f "$MARKER" ] || exit 0
last="$(cat "$MARKER" 2>/dev/null || echo 0)"
is_num "$last" && [ "$last" -gt 0 ] || exit 0

now="$(date +%s)"
# Not settled yet — a restart landed within the debounce window; wait for the next tick.
[ $((now - last)) -ge "$DEBOUNCE" ] || exit 0

# Already reminded for this (or a newer) restart episode — nothing to do.
prev="$(cat "$SENT" 2>/dev/null || echo 0)"
is_num "$prev" || prev=0
[ "$prev" -ge "$last" ] && exit 0

when="$(date -d "@$last" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || echo "@$last")"

# E3: post-restart smoke can false-FAIL if Funnel/edge is still settling even
# after the debounce window, or if the unit is mid-restart. Preflight service,
# use longer retries, and on first fail wait + re-smoke once before writing FAIL.
SMOKE_LINE="smoke: not run"
SMOKE_RC=1
SMOKE_ATTEMPTS=0
SVC_STATE="$(systemctl is-active grok-mcp.service 2>/dev/null || echo unknown)"

run_smoke() {
  local retries="$1" sleep_s="$2"
  SMOKE_ATTEMPTS=$((SMOKE_ATTEMPTS + 1))
  SMOKE_OUT="$(RETRIES="$retries" SLEEP_SECS="$sleep_s" bash /root/astra-config/scripts/smoke-test.sh 2>&1)" && return 0
  return 1
}

redact_smoke() {
  printf '%s\n' "$1" | sed -E 's|https://[^ ]+|/…/MCP_PATH|g'
}

if [ "$SVC_STATE" != "active" ]; then
  SMOKE_LINE="smoke: SKIP (grok-mcp.service is $SVC_STATE — not active yet)"
  SMOKE_RC=1
elif [ -x /root/astra-config/scripts/smoke-test.sh ]; then
  # Debounce already waited DEBOUNCE_SECS; still give Funnel room (8×3s ≈ 24s).
  if run_smoke 8 3; then
    SMOKE_RC=0
  else
    # Second chance after a quiet pause — absorbs edge cert / path blips.
    sleep 15
    if run_smoke 5 3; then
      SMOKE_RC=0
      SMOKE_LINE_NOTE=" (recovered on 2nd attempt after 15s)"
    else
      SMOKE_RC=1
    fi
  fi
  if [ "${SMOKE_RC:-0}" -eq 0 ]; then
    SMOKE_LINE="$(redact_smoke "$SMOKE_OUT" | grep -E 'smoke-test: PASS' | tail -1)"
    [ -n "$SMOKE_LINE" ] || SMOKE_LINE="smoke: PASS (funnel+env path)"
    SMOKE_LINE="${SMOKE_LINE}${SMOKE_LINE_NOTE:-}"
  else
    SMOKE_LINE="$(redact_smoke "$SMOKE_OUT" | grep -E 'smoke-test: FAIL|FAIL —' | tail -1)"
    [ -n "$SMOKE_LINE" ] || SMOKE_LINE="smoke: FAIL (funnel or MCP_PATH)"
  fi
fi

{
  echo "⚡ GROK-MCP RESTART REMINDER (settled as of $(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo "   Last restart: $when (epoch $last)"
  echo "   Service: grok-mcp.service is $SVC_STATE"
  echo "   Box funnel (env MCP_PATH): $SMOKE_LINE"
  echo "   Smoke rounds: $SMOKE_ATTEMPTS (EXPECTED_TOOLS auto-derived from toolSurface unless overridden)"
  echo "   Policy: CHECK connectors after every restart; ROTATE only as needed."
  echo "   • Funnel PASS only proves the box path — NOT that claude.ai/Grok/journaling still point at it."
  echo "   • If claude.ai / Grok / journaling still work → do nothing."
  echo "   • If a consumer is dead → re-add; if still dead or Grok tool list stale →"
  echo "       sudo bash /root/astra-config/scripts/rotate-url.sh"
  echo "     then re-add journaling, claude.ai, and Grok."
  echo "   • New/changed tools always need a rotation (Grok per-URL tool cache)."
  echo "   Dismiss: rm -f $ALERT"
} > "$ALERT"

echo "$last" > "$SENT"
echo "restart-reminder: wrote in-session alert -> $ALERT ($SMOKE_LINE)"
