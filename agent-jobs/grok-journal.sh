#!/usr/bin/env bash
# grok-journal — agent-run wrapper around scripts/grok-journal-run.sh
# Prefer the dedicated systemd unit; this exists for manual agent-run parity.
set -euo pipefail
REPO="${AGENT_REPO:-/root/astra-config}"
exec bash "$REPO/scripts/grok-journal-run.sh" "$@"
