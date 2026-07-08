#!/usr/bin/env bash
# journal-trigger.sh -- usage-gated scheduler runner for the zazesty/Journaling
# daily entry. Replaces the old once-daily cloud trigger.
#
# This script only decides WHEN to fire and POSTs the routine's /fire webhook.
# The actual journaling session runs in Claude Code on the web (billed to the
# plan), where the repo's CLAUDE.md + SessionStart hooks write the entry.
#
# Policy (2026-07-08):
#   • Gate PASS  → fire (0–n times per night as ticks allow; "more if usage permits")
#   • Gate FAIL usage (over pace / 5h ceiling) + no fire yet today (PT) → still fire
#     once ("at least once nightly" floor). Marked fire_reason=nightly_floor.
#   • Gate FAIL auth (http_401 / refresh dead) → never fire; oauth-watch accumulates.
#
# Usage: journal-trigger.sh [--dry-run] [--force]
#   --dry-run  do everything except the actual POST (logs decision as DRYRUN)
#   --force    fire regardless of the gate (one-shot manual/e2e test); the live
#              gate values are still evaluated and logged for the record

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE_SH="${SCRIPT_DIR}/usage-gate.sh"

CONFIG_DIR="${HOME}/.config/journal-trigger"
SECRET_FILE="${CONFIG_DIR}/secret"      # routine /fire bearer token (mode 600)
ENDPOINT_FILE="${CONFIG_DIR}/endpoint"  # routine /fire URL          (mode 600)
LOG="${HOME}/.local/state/journal-cron.log"
FIRED_TODAY="${HOME}/.local/state/journal-fired-pt-date"

BETA_HEADER="experimental-cc-routine-2026-04-01"
TZPT="America/Los_Angeles"

DRY_RUN=0; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force)   FORCE=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$LOG")"
log_line() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

TODAY_PT=$(TZ="$TZPT" date +%F)
HOUR_PT=$(TZ="$TZPT" date +%H)

already_fired_today() {
  [ -f "$FIRED_TODAY" ] && [ "$(cat "$FIRED_TODAY" 2>/dev/null)" = "$TODAY_PT" ]
}

mark_fired_today() {
  printf '%s\n' "$TODAY_PT" >"$FIRED_TODAY"
}

# Auth-shaped gate failures must never take the nightly floor (no usage signal).
is_auth_fail_reason() {
  case "$1" in
    http_401|http_403|refresh_failed|no_token|missing_cred*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- run the usage gate --------------------------------------------------
GATE_OUT=$("$GATE_SH" 2>>"$LOG")
GATE_RC=$?
[ "$GATE_RC" -eq 0 ] && GATE_STATE=pass || GATE_STATE=fail
GATE_REASON="$(printf '%s' "$GATE_OUT" | tr ' ' '\n' | sed -n 's/^gate_reason=//p' | head -1)"

CTX="tick hour=${HOUR_PT} pt_date=${TODAY_PT} gate=${GATE_STATE} ${GATE_OUT}"

# --- decide --------------------------------------------------------------
if [ "$FORCE" = 1 ]; then
  FIRE_REASON=forced
elif [ "$GATE_RC" -eq 0 ]; then
  FIRE_REASON=gate_pass
  /root/astra-config/scripts/journal-oauth-watch.sh >/dev/null 2>&1 || true
elif is_auth_fail_reason "$GATE_REASON"; then
  log_line "${CTX} decision=skip"
  /root/astra-config/scripts/journal-oauth-watch.sh >/dev/null 2>&1 || true
  exit 0
elif already_fired_today; then
  # Usage over pace / ceiling, but we already met the nightly floor.
  log_line "${CTX} decision=skip skip_reason=usage_throttle_floor_met"
  exit 0
else
  # Usage throttle but zero fires so far today → floor fire.
  FIRE_REASON=nightly_floor
  log_line "${CTX} decision=floor_eligible fire_reason=${FIRE_REASON}"
fi

# --- fire ----------------------------------------------------------------
if [ "$DRY_RUN" = 1 ]; then
  log_line "${CTX} decision=fire fire_reason=${FIRE_REASON} post_http=DRYRUN"
  # dry-run does not mark fired-today (so a real tick can still floor)
  exit 0
fi

if [ ! -r "$SECRET_FILE" ] || [ ! -r "$ENDPOINT_FILE" ]; then
  log_line "${CTX} decision=fire fire_reason=${FIRE_REASON} post_http=ERR reason=missing_secret_or_endpoint"
  exit 1
fi
URL=$(cat "$ENDPOINT_FILE")
TOKEN=$(cat "$SECRET_FILE")
# No `text` payload: the routine's saved prompt (the same one the old daily
# SCHEDULE trigger ran with no payload) + the repo's SessionStart hooks +
# CLAUDE.md fully drive the entry. A commanding "write the entry" text overrides
# the session's own judgment and can force a duplicate when one already exists,
# so we send an empty body and let the saved prompt decide.
PAYLOAD='{}'

RESP=$(curl -sS -m 30 -w $'\n%{http_code}' -X POST "$URL" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "anthropic-beta: ${BETA_HEADER}" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>>"$LOG")
CURL_RC=$?
HTTP=$(printf '%s' "$RESP" | tail -n1)
RBODY=$(printf '%s' "$RESP" | sed '$d')
SESSION=$(printf '%s' "$RBODY" | jq -r '.claude_code_session_url // empty' 2>/dev/null)

if [ "$CURL_RC" -ne 0 ]; then
  log_line "${CTX} decision=fire fire_reason=${FIRE_REASON} post_http=ERR curl_rc=${CURL_RC}"
  exit 1
fi
log_line "${CTX} decision=fire fire_reason=${FIRE_REASON} post_http=${HTTP} session=${SESSION:-none}"
if [ "$HTTP" = 200 ] || [ "$HTTP" = 201 ]; then
  mark_fired_today
  exit 0
fi
exit 1
