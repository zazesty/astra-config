#!/usr/bin/env bash
# notify-pushover.sh — one push via Pushover API (Budget Bot interrupts).
#   notify-pushover.sh "title" "message" [priority]
#   notify-pushover.sh "message"                 # title defaults to Budget Bot
#   TITLE=... PRIORITY=1 notify-pushover.sh "msg"
#
# Env (from /etc/hermes-finance.env, chmod 600):
#   PUSHOVER_TOKEN   application API token
#   PUSHOVER_USER    user key
# Optional:
#   PUSHOVER_DEVICE  restrict to one device name
#   PUSHOVER_SOUND   e.g. pushover, siren (default: pushover)
#
# Priority (Pushover):
#   -2 silent  -1 quiet  0 normal  1 high (bypass quiet hours)
#    2 emergency (requires retry+expire; confirmation loop)
#
# Fail-open: missing config / API error → log + exit 0 (never break callers).
set -uo pipefail

TITLE="${TITLE:-Budget Bot}"
BODY=""
PRIORITY="${PRIORITY:-${PUSHOVER_PRIORITY:-0}}"

if [ "$#" -ge 2 ]; then
  TITLE="$1"
  BODY="$2"
  if [ "$#" -ge 3 ]; then
    PRIORITY="$3"
  fi
elif [ "$#" -eq 1 ]; then
  BODY="$1"
elif [ ! -t 0 ]; then
  BODY="$(cat)"
fi
BODY="${BODY:-}"
TITLE="${TITLE:-Budget Bot}"

ENV_FILE="${PUSHOVER_ENV:-}"
if [ -z "$ENV_FILE" ]; then
  if [ -f /etc/hermes-finance.env ]; then
    ENV_FILE=/etc/hermes-finance.env
  else
    ENV_FILE=/etc/grok-mcp.env
  fi
fi
LOG="${PUSHOVER_LOG:-/root/.local/state/hermes-finance/notify.log}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

set -a
# shellcheck disable=SC1090
. "$ENV_FILE" 2>/dev/null || true
set +a

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s pushover %s\n' "$(stamp)" "$*" >>"$LOG"; }

if [ -z "${PUSHOVER_TOKEN:-}" ] || [ -z "${PUSHOVER_USER:-}" ]; then
  log "skip missing PUSHOVER_TOKEN/USER"
  exit 0
fi

# Keep payload reasonable
if [ "${#BODY}" -gt 900 ]; then
  BODY="${BODY:0:890}…"
fi
if [ "${#TITLE}" -gt 200 ]; then
  TITLE="${TITLE:0:190}…"
fi

# Validate priority is integer-ish
case "$PRIORITY" in
  -2|-1|0|1|2) ;;
  *) PRIORITY=0 ;;
esac

ARGS=(
  --form-string "token=${PUSHOVER_TOKEN}"
  --form-string "user=${PUSHOVER_USER}"
  --form-string "title=${TITLE}"
  --form-string "message=${BODY}"
  --form-string "priority=${PRIORITY}"
)

# Emergency: re-notify every 60s for up to 10 min until acked
if [ "$PRIORITY" = "2" ]; then
  ARGS+=(--form-string "retry=60" --form-string "expire=600")
fi

if [ -n "${PUSHOVER_DEVICE:-}" ]; then
  ARGS+=(--form-string "device=${PUSHOVER_DEVICE}")
fi
if [ -n "${PUSHOVER_SOUND:-}" ]; then
  ARGS+=(--form-string "sound=${PUSHOVER_SOUND}")
elif [ "$PRIORITY" = "2" ]; then
  ARGS+=(--form-string "sound=siren")
elif [ "$PRIORITY" = "1" ]; then
  ARGS+=(--form-string "sound=persistent")
fi

RESP=$(curl -sS --max-time 20 \
  "${ARGS[@]}" \
  -w "\nHTTP:%{http_code}" \
  https://api.pushover.net/1/messages.json 2>&1) || true
CODE=$(printf '%s\n' "$RESP" | sed -n 's/^HTTP://p' | tail -1)
if [ "$CODE" = "200" ]; then
  log "sent http=$CODE priority=$PRIORITY title_len=${#TITLE} msg_len=${#BODY}"
  exit 0
fi
log "error http=${CODE:-?} priority=$PRIORITY body=$(printf '%s' "$RESP" | tr '\n' ' ' | cut -c1-220)"
exit 0
