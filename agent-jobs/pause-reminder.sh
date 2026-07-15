#!/usr/bin/env bash
# pause-reminder — body for grok journal autopilot pause nudge (via agent-run).
# Wrapper around scripts/grok-journal-pause-reminder.sh for irritation #4:
# one runner convention, not a one-off service script path only.
set -euo pipefail
REPO="${AGENT_REPO:-/root/astra-config}"
exec bash "$REPO/scripts/grok-journal-pause-reminder.sh" "$REPO"
