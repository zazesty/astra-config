#!/usr/bin/env bash
# Weekly AI *usage* digest — consumption, not prepaid reloads.
# Email via Resend. Fail-open. Stdout is the Photon/cron body.
set -uo pipefail
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="/root/hermes-finance${PYTHONPATH:+:$PYTHONPATH}"
BODY="$(python3 /root/astra-config/scripts/ai_usage_digest.py)" || BODY=""
SUBJECT="Budget Bot: weekly AI usage $(date +%Y-%m-%d)"
if [ -z "${BODY// }" ]; then
  exit 0
fi
if [ "${SKIP_EMAIL:-0}" != "1" ]; then
  printf '%s\n' "$BODY" | /root/astra-config/scripts/notify-email.sh "$SUBJECT" || true
fi
printf '%s\n' "$BODY"
