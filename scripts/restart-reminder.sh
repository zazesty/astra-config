#!/usr/bin/env bash
# =============================================================================
# restart-reminder.sh — debounced "grok-mcp restarted" nudge.
#
# grok-mcp.service's ExecStartPost stamps $STATE_DIRECTORY/restart-marker (epoch)
# on every (re)start. This script runs on a short timer and emails ONCE, only
# after the restarts have SETTLED (no new restart within DEBOUNCE_SECS) — so a
# burst of deploy restarts collapses into a single reminder.
#
# It nudges toward a URL ROTATION (not just a reconnect): a restart drops the
# live session every remote MCP connector holds, and Grok's connector caches per
# URL, so re-adding the SAME url won't refresh it — a fresh URL is the reliable
# fix. See README "Rotating the URL".
# =============================================================================
set -uo pipefail

MARKER="${RESTART_MARKER:-/var/lib/grok-mcp/restart-marker}"
SENT="${MARKER}.reminded"
DEBOUNCE="${DEBOUNCE_SECS:-600}"        # wait this long after the last restart
HERE="$(cd "$(dirname "$0")" && pwd)"

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
BODY="grok-mcp restarted and has stayed up (last restart: $when).

Heads-up: a restart drops the live session that every remote MCP connector holds,
so the claude.ai, Grok, and journaling-routine connectors are likely dead now.
Grok's connector caches per URL, so re-adding the SAME url won't refresh it — the
reliable fix is to ROTATE the URL and reconnect all three:

  sudo bash /root/astra-config/scripts/rotate-url.sh

(If you just did a burst of restarts, this is the single settled reminder for it.)"

printf '%s\n' "$BODY" | bash "$HERE/notify-email.sh" "grok-mcp restarted — rotate URL + reconnect connectors"
echo "$last" > "$SENT"
