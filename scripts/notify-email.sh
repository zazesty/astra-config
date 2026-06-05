#!/usr/bin/env bash
# =============================================================================
# notify-email.sh — send one alert email via the Resend API.
#   notify-email.sh "Subject line" [body-file]      # body from file, or...
#   echo "body" | notify-email.sh "Subject line"    # body from stdin
#
# Config (all from /etc/grok-mcp.env, chmod 600, NEVER in the repo):
#   RESEND_API_KEY    required; absent => safe no-op (logs + exit 0, never breaks callers)
#   NOTIFY_EMAIL_TO   default zazesty@gmail.com
#   NOTIFY_EMAIL_FROM default onboarding@resend.dev  (Resend's no-domain sender;
#                     it can ONLY deliver to the Resend account's own signup
#                     address — fine for self-alerts. Use a verified-domain
#                     address here to send elsewhere.)
#
# Deliberately fail-OPEN: a missing key or a Resend outage must never abort the
# caller (health check, push timer). It logs to $NOTIFY_LOG and returns 0.
# =============================================================================
set -uo pipefail

SUBJECT="${1:-(no subject)}"
BODY_FILE="${2:-}"
ENV_FILE="${NOTIFY_ENV:-/etc/grok-mcp.env}"
LOG="${NOTIFY_LOG:-/root/.astra-notify.log}"

set -a; . "$ENV_FILE" 2>/dev/null || true; set +a
TO="${NOTIFY_EMAIL_TO:-zazesty@gmail.com}"
FROM="${NOTIFY_EMAIL_FROM:-onboarding@resend.dev}"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log()   { printf '%s %s\n' "$(stamp)" "$*" >> "$LOG"; }

# Body: from file arg, else stdin (non-tty), else empty.
if [ -n "$BODY_FILE" ] && [ -f "$BODY_FILE" ]; then
  BODY="$(cat "$BODY_FILE")"
elif [ ! -t 0 ]; then
  BODY="$(cat)"
else
  BODY=""
fi

if [ -z "${RESEND_API_KEY:-}" ]; then
  log "SKIP (no RESEND_API_KEY) subject=$SUBJECT"
  echo "notify-email: RESEND_API_KEY not set in $ENV_FILE — skipped (no-op)." >&2
  exit 0
fi

# Build the JSON payload safely (python handles escaping of subject/body).
PAYLOAD="$(SUBJECT="$SUBJECT" BODY="$BODY" FROM="$FROM" TO="$TO" python3 -c '
import json, os
print(json.dumps({
    "from": os.environ["FROM"],
    "to": [os.environ["TO"]],
    "subject": os.environ["SUBJECT"],
    "text": os.environ["BODY"],
}))')"

HTTP="$(curl -s -o /tmp/.resend.out -w '%{http_code}' --max-time 20 \
          -X POST 'https://api.resend.com/emails' \
          -H "Authorization: Bearer $RESEND_API_KEY" \
          -H 'Content-Type: application/json' \
          -d "$PAYLOAD" 2>/dev/null || echo 000)"

if [ "$HTTP" = "200" ] || [ "$HTTP" = "201" ]; then
  log "SENT subject=$SUBJECT to=$TO http=$HTTP"
  echo "notify-email: sent ($HTTP) to $TO"
  exit 0
fi

log "FAIL http=$HTTP subject=$SUBJECT resp=$(tr -d '\n' < /tmp/.resend.out 2>/dev/null | head -c 300)"
echo "notify-email: send FAILED (http=$HTTP) — see $LOG" >&2
exit 0   # fail-open: never abort the caller
