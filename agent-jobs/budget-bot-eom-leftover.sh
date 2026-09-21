#!/usr/bin/env bash
# Monthly Budget Bot leftover congrats — 1st of month 09:00 PT.
# leftover = prior-month calendar safe-to-spend. No daily digest.
set -euo pipefail

BUDGET_BOT_ROOT="${BUDGET_BOT_ROOT:-${HERMES_ROOT:-/root/budget-bot}}"
export BUDGET_BOT_STATE="${BUDGET_BOT_STATE:-${HERMES_FINANCE_STATE:-$HOME/.local/state/budget-bot}}"
export HERMES_FINANCE_STATE="$BUDGET_BOT_STATE"
export PYTHONPATH="${BUDGET_BOT_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
cd "$BUDGET_BOT_ROOT"
exec python3 -m budget_bot eom-leftover
