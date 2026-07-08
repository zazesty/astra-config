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
{
  echo "⚡ GROK-MCP RESTART REMINDER (settled as of $(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo "   Last restart: $when"
  echo "   Policy: CHECK connectors after every restart; ROTATE only as needed."
  echo "   • If claude.ai / Grok / journaling still work → do nothing."
  echo "   • If a consumer is dead → re-add; if still dead or Grok tool list stale →"
  echo "       sudo bash /root/astra-config/scripts/rotate-url.sh"
  echo "     then re-add journaling, claude.ai, and Grok."
  echo "   • New/changed tools always need a rotation (Grok per-URL tool cache)."
  echo "   Dismiss: rm -f $ALERT   (or it clears on next clean cycle when you --ack via no-op)"
} > "$ALERT"

echo "$last" > "$SENT"
echo "restart-reminder: wrote in-session alert -> $ALERT"
