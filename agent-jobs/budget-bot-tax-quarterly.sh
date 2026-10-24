#!/usr/bin/env bash
# Quarterly Budget Bot tax review — 1st of Jan/Apr/Jul/Oct, 09:00 PT.
# Email only. The command itself stays quiet outside the first 7 days,
# so a Persistent catch-up after the grace window does not send.
set -euo pipefail

BUDGET_BOT_ROOT="${BUDGET_BOT_ROOT:-${HERMES_ROOT:-/root/budget-bot}}"
export BUDGET_BOT_STATE="${BUDGET_BOT_STATE:-${HERMES_FINANCE_STATE:-$HOME/.local/state/budget-bot}}"
export HERMES_FINANCE_STATE="$BUDGET_BOT_STATE"
export PYTHONPATH="${BUDGET_BOT_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
cd "$BUDGET_BOT_ROOT"
exec python3 -m budget_bot tax-quarterly
