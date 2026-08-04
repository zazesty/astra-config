#!/usr/bin/env bash
# Bind Tailscale Funnel secret path → local Plaid webhook server.
# Path secret from PLAID_WEBHOOK_SECRET in /etc/hermes-finance.env (generated on first serve).
set -euo pipefail
ENV_FILE="${HERMES_PLAID_ENV:-/etc/hermes-finance.env}"
PORT="${PLAID_WEBHOOK_PORT:-8766}"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE" 2>/dev/null || true
set +a

if [ -z "${PLAID_WEBHOOK_SECRET:-}" ]; then
  # Generate once so Funnel + Plaid register agree before python starts
  PLAID_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  printf '\n# Budget Bot Plaid webhook path secret\nPLAID_WEBHOOK_SECRET=%s\n' \
    "$PLAID_WEBHOOK_SECRET" >>"$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
fi

MOUNT="/plaid-wh-${PLAID_WEBHOOK_SECRET}"
# serve + funnel so path is on the public HTTPS host
for cmd in serve funnel; do
  tailscale "$cmd" --bg --yes --set-path="$MOUNT" "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true
done
echo "funnel mount=$MOUNT -> 127.0.0.1:${PORT}"
