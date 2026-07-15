#!/usr/bin/env bash
# journal-trigger.sh -- usage-gated scheduler runner for the zazesty/Journaling
# daily entry. Replaces the old once-daily cloud trigger.
#
# This script only decides WHEN to fire and POSTs the routine's /fire webhook.
# The actual journaling session runs in Claude Code on the web (billed to the
# plan), where the repo's CLAUDE.md + SessionStart hooks write the entry.
#
# Policy (2026-07-14, revised):
#   • Hard stop: no auto-fires at or after 2026-07-19 21:00 America/Los_Angeles
#     (inclusive). --force still allowed for one-shot manual admin use.
#   • Gate PASS (we know usage is under pace) → fire every tick 1–6am
#     ("more if usage permits"). No daily floor — zero-entry nights are OK.
#   • Gate FAIL usage (we KNOW over weekly pace / 5h ceiling) → NEVER fire.
#   • Gate FAIL auth (we DON'T know usage — 401 / refresh dead) → NEVER fire.
#     (Removed 2026-07-14: former auth_unknown_floor once-per-PT-day fire.)
#     oauth-watch still tracks ≥48h email.
#
# Usage: journal-trigger.sh [--dry-run] [--force]
#   --dry-run  do everything except the actual POST (logs decision as DRYRUN)
#   --force    fire regardless of the gate (one-shot manual/e2e test); the live
#              gate values are still evaluated and logged for the record.
#              Bypasses the hard-stop cutoff (admin override only).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE_SH="${SCRIPT_DIR}/usage-gate.sh"

CONFIG_DIR="${HOME}/.config/journal-trigger"
SECRET_FILE="${CONFIG_DIR}/secret"      # routine /fire bearer token (mode 600)
ENDPOINT_FILE="${CONFIG_DIR}/endpoint"  # routine /fire URL          (mode 600)
LOG="${HOME}/.local/state/journal-cron.log"
# PT date of last successful fire (audit trail; no longer used as a floor).
FIRED_TODAY="${HOME}/.local/state/journal-fired-pt-date"

BETA_HEADER="experimental-cc-routine-2026-04-01"
TZPT="America/Los_Angeles"
# Inclusive hard stop: no auto ticks at/after this instant (PT).
STOP_AFTER_PT="2026-07-19 21:00:00"

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
NOW_EPOCH=$(date +%s)
STOP_EPOCH=$(TZ="$TZPT" date -d "$STOP_AFTER_PT" +%s)

mark_fired_today() {
  printf '%s\n' "$TODAY_PT" >"$FIRED_TODAY"
}

# Auth-shaped failures = usage unknown (cannot read /usage).
is_auth_fail_reason() {
  case "$1" in
    http_401|http_403|refresh_failed|no_token|missing_cred*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- hard stop (auto only; --force bypasses for admin) -------------------
if [ "$FORCE" != 1 ] && [ "$NOW_EPOCH" -ge "$STOP_EPOCH" ]; then
  log_line "tick hour=${HOUR_PT} pt_date=${TODAY_PT} decision=skip skip_reason=hard_stop_after_cutoff stop_after_pt=${STOP_AFTER_PT}"
  exit 0
fi

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
  # Healthy oauth path: clear dead-clock + opportunistic --refresh (≤1/12h).
  /root/astra-config/scripts/journal-oauth-watch.sh >/dev/null 2>&1 || true
elif is_auth_fail_reason "$GATE_REASON"; then
  # Usage UNKNOWN — do NOT fire (no daily floor). Watch/email still tracks auth.
  /root/astra-config/scripts/journal-oauth-watch.sh >/dev/null 2>&1 || true
  log_line "${CTX} decision=skip skip_reason=auth_unknown_no_floor"
  exit 0
else
  # Usage KNOWN and over budget — never fire.
  log_line "${CTX} decision=skip skip_reason=usage_over_budget"
  exit 0
fi

# --- fire ----------------------------------------------------------------
if [ "$DRY_RUN" = 1 ]; then
  log_line "${CTX} decision=fire fire_reason=${FIRE_REASON} post_http=DRYRUN"
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
# CLAUDE.md fully drive the entry.
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
