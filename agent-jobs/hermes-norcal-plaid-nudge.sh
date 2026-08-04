#!/usr/bin/env bash
# Weekly nudge: check whether 1st Nor Cal Plaid Link works again.
# Armed via hermes-norcal-plaid-nudge.timer (Fridays 11:00 America/Los_Angeles).
# Survives agent sessions — pure box systemd + Resend email.
set -euo pipefail

NOTIFY="${HERMES_NOTIFY_CMD:-/root/astra-config/scripts/notify-email.sh}"
HERMES_ROOT="${HERMES_ROOT:-/root/hermes-finance}"
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="${HERMES_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

STATUS_JSON="{}"
if [ -d "$HERMES_ROOT" ]; then
  STATUS_JSON="$(cd "$HERMES_ROOT" && python3 -m hermes_finance plaid-status 2>/dev/null || echo '{}')"
fi

SUBJECT="Budget Bot: check 1st Nor Cal ↔ Plaid (weekly)"
BODY="$(cat <<EOF
Friday Plaid / NorCal reminder (Budget Bot)

What to do (2 min):
1. Ask the box (or open a session) to run Plaid Link with Funnel for Budget Bot.
2. In the Link UI, search "1st Northern California" and try to connect.
3. If it succeeds: reply "norcal linked" so webhooks can be registered + first sync.
4. If it still fails: ignore until next Friday (or drop the monthly CU statement PDF for import).

Why: PayPal is live via Plaid webhooks. NorCal's connector was borked; CU spend only updates when you import a statement or Plaid starts working.

Current linked Items (no secrets):
${STATUS_JSON}

Optional: hassle 1st Nor Cal support that third-party/Plaid aggregation still fails.

— zaz-astra · hermes-norcal-plaid-nudge.timer
EOF
)"

printf '%s\n' "$BODY" | "$NOTIFY" "$SUBJECT"
echo "nudge sent subject=$SUBJECT"
