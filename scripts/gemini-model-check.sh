#!/usr/bin/env bash
# =============================================================================
# gemini-model-check.sh — daily "does gemini-pro-latest still resolve to 3.1 Pro?"
#
# `gemini-pro-latest` is a MOVING alias. Google is expected to repoint it (e.g.
# to a 3.5 Pro family) at a higher price point — a silent COST jump. This probe
# asks the API what the alias actually resolves to (response.modelVersion) and
# EMAILS once when it stops matching the expected family. It never changes the
# pinned model; the operator decides whether to accept the new one (--ack).
#
# Fires when: resolved modelVersion does NOT start with the expected prefix
#   (default "gemini-3.1-pro"; override via state/gemini-model.expected, written
#    by --ack, or the GEMINI_EXPECTED env var).
#
# Fail-SAFE: a failed/empty probe (offline, bad key, quota) exits non-zero
# WITHOUT emailing, so a transient outage can't masquerade as a model change.
#
# Usage:
#   gemini-model-check.sh [REPO]   # run the check (default REPO=/root/astra-config)
#   gemini-model-check.sh --ack    # accept the CURRENT resolved model as expected
#                                  # and clear the alert (run after an intentional bump)
# =============================================================================
set -uo pipefail

REPO="/root/astra-config"
ACK=""
case "${1:-}" in
  --ack) ACK=1 ;;
  "" ) ;;
  * ) REPO="$1" ;;
esac

ENV_FILE="${GEMINI_ENV:-/etc/grok-mcp.env}"
STATE_DIR="$REPO/state"
EXPECTED_FILE="$STATE_DIR/gemini-model.expected"
ALERT="${GEMINI_MODEL_ALERT:-/root/.gemini-model.alert}"
NOTIFY="${NOTIFY_CMD:-$REPO/scripts/notify-email.sh}"
MODEL="${GEMINI_PROBE_MODEL:-gemini-pro-latest}"
DEFAULT_EXPECTED="gemini-3.1-pro"

mkdir -p "$STATE_DIR"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- load GEMINI_API_KEY (sourced; file is chmod 600, never echoed) -----------
set -a; . "$ENV_FILE" 2>/dev/null || true; set +a
[ -n "${GEMINI_API_KEY:-}" ] || { echo "gemini-model-check: no GEMINI_API_KEY in $ENV_FILE" >&2; exit 1; }

# --- what does the alias actually resolve to right now? ------------------------
RESOLVED="$(curl -s "https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${GEMINI_API_KEY}" \
  -H 'Content-Type: application/json' -d '{"contents":[{"parts":[{"text":"hi"}]}]}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("modelVersion") or "")' 2>/dev/null)"
[ -n "$RESOLVED" ] || { echo "gemini-model-check: probe failed/empty (offline? bad key? quota?)" >&2; exit 1; }

# --- expected prefix: env override > state file > built-in default -------------
EXPECTED="${GEMINI_EXPECTED:-}"
[ -z "$EXPECTED" ] && [ -r "$EXPECTED_FILE" ] && EXPECTED="$(cat "$EXPECTED_FILE")"
[ -z "$EXPECTED" ] && EXPECTED="$DEFAULT_EXPECTED"

if [ -n "$ACK" ]; then
  printf '%s\n' "$RESOLVED" > "$EXPECTED_FILE"
  rm -f "$ALERT"
  echo "gemini-model-check: acknowledged — expected set to '$RESOLVED', alert cleared."
  exit 0
fi

case "$RESOLVED" in
  "$EXPECTED"*)
    rm -f "$ALERT"
    echo "gemini-model-check: OK ($MODEL -> $RESOLVED, matches '${EXPECTED}*')"
    ;;
  *)
    # flip detected — email once per distinct resolved value (anti-spam)
    PREV=""; [ -r "$ALERT" ] && PREV="$(cat "$ALERT")"
    echo "gemini-model-check: CHANGE — $MODEL -> $RESOLVED (expected '${EXPECTED}*')"
    if [ "$PREV" != "$RESOLVED" ]; then
      printf '%s\n' "$RESOLVED" > "$ALERT"
      BODY="$(printf '%s now resolves to:\n  %s\n\nExpected family: %s*\nDetected at: %s\n\nThis is likely a silent model (and possibly PRICE) change.\nIf intentional, accept it to stop alerts:\n  bash %s/scripts/gemini-model-check.sh --ack\n' \
        "$MODEL" "$RESOLVED" "$EXPECTED" "$(stamp)" "$REPO")"
      printf '%s' "$BODY" | "$NOTIFY" "⚠️ Gemini model changed: $RESOLVED (was ${EXPECTED}*)"
      echo "gemini-model-check: alert email dispatched."
    else
      echo "gemini-model-check: already alerted for $RESOLVED — not re-emailing."
    fi
    ;;
esac
