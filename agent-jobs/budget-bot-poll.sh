#!/usr/bin/env bash
# budget-bot-poll — backup near-instant path (no digest).
# Webhooks are primary; this covers missed webhooks every N minutes.
set -euo pipefail

BUDGET_BOT_ROOT="${BUDGET_BOT_ROOT:-${HERMES_ROOT:-/root/budget-bot}}"
export BUDGET_BOT_STATE="${BUDGET_BOT_STATE:-${HERMES_FINANCE_STATE:-$HOME/.local/state/budget-bot}}"
export HERMES_FINANCE_STATE="$BUDGET_BOT_STATE"
export PYTHONPATH="${BUDGET_BOT_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

cd "$BUDGET_BOT_ROOT"
exec python3 -m budget_bot plaid-webhook-process
