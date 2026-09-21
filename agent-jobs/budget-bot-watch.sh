#!/usr/bin/env bash
# budget-bot-watch — optional backup job (webhooks + 15m poll are primary).
# No daily digests. Rare hardcap/pace interrupts only when notify_enabled / BUDGET_BOT_LIVE.
set -euo pipefail

BUDGET_BOT_ROOT="${BUDGET_BOT_ROOT:-${HERMES_ROOT:-/root/budget-bot}}"
export BUDGET_BOT_STATE="${BUDGET_BOT_STATE:-${HERMES_FINANCE_STATE:-$HOME/.local/state/budget-bot}}"
export HERMES_FINANCE_STATE="$BUDGET_BOT_STATE"
export PYTHONPATH="${BUDGET_BOT_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

cd "$BUDGET_BOT_ROOT"

ARGS=(watch)

if [ "${BUDGET_BOT_LIVE:-${HERMES_LIVE:-0}}" = "1" ]; then
  ARGS+=(--live)
fi

AS_OF="${BUDGET_BOT_AS_OF:-${HERMES_AS_OF:-}}"
if [ -n "$AS_OF" ]; then
  ARGS+=(--as-of "$AS_OF")
fi

python3 -m budget_bot "${ARGS[@]}"
