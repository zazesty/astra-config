#!/usr/bin/env bash
# Bind a secret Funnel path to the buy-queue Yes/No page. Does not print the path.
set -euo pipefail
export HOME="${HOME:-/root}"
PORT="${BUY_QUEUE_ASK_PORT:-8773}"
cd /root/budget-bot
SECRET="$(python3 -c 'from budget_bot.buy_queue_ask import ensure_secret; print(ensure_secret())')"
MOUNT="/bq-${SECRET}"
for cmd in serve funnel; do
  tailscale "$cmd" --bg --yes --set-path="$MOUNT" "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true
done
echo "buy-queue-ask funnel bound port=${PORT}"
