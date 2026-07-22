#!/usr/bin/env bash
# hermes-finance-watch — budget rules + digest/anomaly alerts.
# Pre-Plaid: runs in fixture mode with dry-run notify by default.
# Enable live email: set notify_enabled true in ~/.local/state/hermes-finance/config.json
#   and/or HERMES_LIVE=1
set -euo pipefail

HERMES_ROOT="${HERMES_ROOT:-/root/hermes-finance}"
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="${HERMES_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

cd "$HERMES_ROOT"

ARGS=(watch --force-digest)
# Morning-ish: force digest so the first run of the day always archives one.
# Dedup still prevents re-emailing the same anomaly keys.

if [ "${HERMES_LIVE:-0}" = "1" ]; then
  ARGS+=(--live)
fi

if [ -n "${HERMES_AS_OF:-}" ]; then
  ARGS+=(--as-of "$HERMES_AS_OF")
fi

python3 -m hermes_finance "${ARGS[@]}"
