#!/usr/bin/env bash
# hermes-finance-watch — optional backup job (webhooks + 15m poll are primary).
# No daily digests. Rare hardcap/pace interrupts only when notify_enabled / HERMES_LIVE.
set -euo pipefail

HERMES_ROOT="${HERMES_ROOT:-/root/hermes-finance}"
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="${HERMES_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

cd "$HERMES_ROOT"

ARGS=(watch)
# never --force-digest (owner: no daily digests)

if [ "${HERMES_LIVE:-0}" = "1" ]; then
  ARGS+=(--live)
fi

if [ -n "${HERMES_AS_OF:-}" ]; then
  ARGS+=(--as-of "$HERMES_AS_OF")
fi

python3 -m hermes_finance "${ARGS[@]}"
