#!/usr/bin/env bash
# Monthly Budget Bot leftover congrats — 1st of month 09:00 PT.
# leftover = prior-month calendar safe-to-spend. No daily digest.
set -euo pipefail

HERMES_ROOT="${HERMES_ROOT:-/root/hermes-finance}"
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="${HERMES_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
cd "$HERMES_ROOT"
exec python3 -m hermes_finance eom-leftover
