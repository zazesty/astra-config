#!/usr/bin/env bash
# notify-sms.sh — send one SMS via Twilio REST API.
#   notify-sms.sh "message body"
#   echo "body" | notify-sms.sh
#
# Env (from /etc/hermes-finance.env or /etc/grok-mcp.env, chmod 600):
#   TWILIO_ACCOUNT_SID
#   TWILIO_AUTH_TOKEN
#   TWILIO_FROM   E.164 e.g. +15551234567
#   TWILIO_TO     E.164 destination
#
# Fail-open: missing config → log + exit 0 (never break callers).
set -uo pipefail

BODY="${1:-}"
if [ -z "$BODY" ] && [ ! -t 0 ]; then
  BODY="$(cat)"
fi
BODY="${BODY:-}"

ENV_FILE="${TWILIO_ENV:-}"
if [ -z "$ENV_FILE" ]; then
  if [ -f /etc/hermes-finance.env ]; then
    ENV_FILE=/etc/hermes-finance.env
  else
    ENV_FILE=/etc/grok-mcp.env
  fi
fi
LOG="${TWILIO_LOG:-/root/.local/state/hermes-finance/notify.log}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

set -a
# shellcheck disable=SC1090
. "$ENV_FILE" 2>/dev/null || true
set +a

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s sms %s\n' "$(stamp)" "$*" >>"$LOG"; }

if [ -z "${TWILIO_ACCOUNT_SID:-}" ] || [ -z "${TWILIO_AUTH_TOKEN:-}" ] \
  || [ -z "${TWILIO_FROM:-}" ] || [ -z "${TWILIO_TO:-}" ]; then
  log "skip missing TWILIO_* env"
  exit 0
fi

# Twilio hard-limits body ~1600; keep SMS short
if [ "${#BODY}" -gt 1400 ]; then
  BODY="${BODY:0:1390}…"
fi

URL="https://api.twilio.com/2010-04-01/Accounts/${TWILIO_ACCOUNT_SID}/Messages.json"
# shellcheck disable=SC2034
RESP=$(curl -sS -u "${TWILIO_ACCOUNT_SID}:${TWILIO_AUTH_TOKEN}" \
  -X POST "$URL" \
  --data-urlencode "From=${TWILIO_FROM}" \
  --data-urlencode "To=${TWILIO_TO}" \
  --data-urlencode "Body=${BODY}" \
  -w "\nHTTP:%{http_code}" 2>&1) || true
CODE=$(printf '%s\n' "$RESP" | sed -n 's/^HTTP://p' | tail -1)
if [ "$CODE" = "201" ] || [ "$CODE" = "200" ]; then
  log "sent http=$CODE to_set=1 len=${#BODY}"
  exit 0
fi
log "error http=${CODE:-?} body=$(printf '%s' "$RESP" | tr '\n' ' ' | cut -c1-200)"
exit 0
