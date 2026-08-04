#!/usr/bin/env bash
# hermes-finance-poll — backup near-instant path (no digest).
# Webhooks are primary; this covers missed webhooks every N minutes.
set -euo pipefail

HERMES_ROOT="${HERMES_ROOT:-/root/hermes-finance}"
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="${HERMES_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

cd "$HERMES_ROOT"
exec python3 -m hermes_finance plaid-webhook-process
