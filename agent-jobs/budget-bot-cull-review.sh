#!/usr/bin/env bash
set -euo pipefail
export BUDGET_BOT_STATE="${BUDGET_BOT_STATE:-/root/.local/state/budget-bot}"
cd /root/budget-bot
exec python3 -m budget_bot cull-review
