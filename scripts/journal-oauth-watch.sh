#!/usr/bin/env bash
# =============================================================================
# journal-oauth-watch.sh — email if Claude OAuth for the journaling usage-gate
# has been broken for >= 48h (refresh/access unusable → gate fail-closed → no
# journal fires). Clears state when the gate succeeds again.
#
# Runs: daily timer + after each journal-trigger gate failure.
# =============================================================================
set -uo pipefail

STATE_DIR="${HOME}/.local/state"
SINCE_F="${STATE_DIR}/journal-oauth-fail-since"
SENT_F="${STATE_DIR}/journal-oauth-alert-sent"
LOG="${STATE_DIR}/journal-cron.log"
GATE="${JOURNAL_GATE:-/root/journal-trigger/usage-gate.sh}"
HERE="$(cd "$(dirname "$0")" && pwd)"
THRESHOLD_SECS="${JOURNAL_OAUTH_ALERT_SECS:-$((48 * 3600))}"  # 48h default

mkdir -p "$STATE_DIR"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s [journal-oauth-watch] %s\n' "$(stamp)" "$*" >>"$LOG"; }

is_num() { case "$1" in ''|*[!0-9]*) return 1;; *) return 0;; esac; }

# Run gate; capture summary line.
GATE_OUT="$("$GATE" 2>>"$LOG")" || true
GATE_RC=$?
# usage-gate prints one summary line on stdout even on fail
REASON="$(printf '%s' "$GATE_OUT" | tr ' ' '\n' | sed -n 's/^gate_reason=//p' | head -1)"
DECISION="$(printf '%s' "$GATE_OUT" | tr ' ' '\n' | sed -n 's/^gate_decision=//p' | head -1)"

# Auth-shaped failures only (not weekly_over_pace / five_hour_ceiling).
is_auth_fail() {
  case "$REASON" in
    http_401|http_403|refresh_failed|no_token|missing_cred*) return 0 ;;
    *) return 1 ;;
  esac
}

if [ "$GATE_RC" -eq 0 ] || [ "$DECISION" = pass ]; then
  if [ -f "$SINCE_F" ] || [ -f "$SENT_F" ]; then
    log "oauth ok again (gate pass) — clearing fail-since / alert stamps"
  fi
  rm -f "$SINCE_F" "$SENT_F"
  # Proactive refresh rotation while healthy: mints new access+refresh so the
  # chain doesn't go stale. Headless `claude -p` does NOT rewrite credentials
  # when access is still valid (verified 2026-07-08); explicit --refresh does.
  LAST_REF="${STATE_DIR}/journal-oauth-last-refresh"
  now="$(date +%s)"
  last_r="$(cat "$LAST_REF" 2>/dev/null || echo 0)"
  is_num "$last_r" || last_r=0
  # At most once per 12h from this watch path (timer is daily; journal ticks also call us).
  if [ $((now - last_r)) -ge $((12 * 3600)) ]; then
    if "$GATE" --refresh >>"$LOG" 2>&1; then
      echo "$now" >"$LAST_REF"
      log "proactive --refresh ok (extends refresh chain)"
    else
      log "proactive --refresh failed (will rely on next gate/login)"
    fi
  fi
  exit 0
fi

if ! is_auth_fail; then
  # Non-auth fail: do not accumulate oauth-dead clock (usage over pace is fine).
  exit 0
fi

now="$(date +%s)"
if [ ! -f "$SINCE_F" ]; then
  echo "$now" >"$SINCE_F"
  log "auth gate fail started (reason=$REASON) — clock starts; email after ${THRESHOLD_SECS}s"
  exit 0
fi

since="$(cat "$SINCE_F" 2>/dev/null || echo 0)"
is_num "$since" || since=0
age=$((now - since))
if [ "$age" -lt "$THRESHOLD_SECS" ]; then
  log "auth still failing (reason=$REASON age=${age}s < ${THRESHOLD_SECS}s) — no email yet"
  exit 0
fi

# Already emailed for this continuous episode?
if [ -f "$SENT_F" ]; then
  sent="$(cat "$SENT_F" 2>/dev/null || echo 0)"
  is_num "$sent" || sent=0
  # Re-nag at most once per 7 days while still broken.
  if [ $((now - sent)) -lt $((7 * 86400)) ]; then
    exit 0
  fi
fi

hours=$((age / 3600))
BODY="Claude OAuth for the journaling usage-gate has been broken for ~${hours}h (threshold 48h).

gate_reason=${REASON}
gate_line=${GATE_OUT}

What this means:
  • journal-trigger ticks are running but FAIL CLOSED (no /fire) — not a usage issue.
  • access token (~8h) and/or refresh token is unusable (invalid_grant / 401).

Fix:
  1. On zaz-astra, run an interactive Claude Code session once so ~/.claude/.credentials.json
     gets a fresh access+refresh pair (or re-login if the CLI prompts).
  2. Verify: /root/journal-trigger/usage-gate.sh   → gate_decision=pass
  3. Optional catch-up: /root/journal-trigger/journal-trigger.sh
  4. After any MCP_PATH rotation, re-point the journaling routine's MCP connector URL.

Log: $LOG
"

printf '%s\n' "$BODY" | bash "$HERE/notify-email.sh" "journaling OAuth dead ≥48h — no journal fires"
echo "$now" >"$SENT_F"
log "emailed oauth-dead alert (age=${age}s reason=$REASON)"
exit 0
