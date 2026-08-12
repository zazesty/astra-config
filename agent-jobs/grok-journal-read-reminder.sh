#!/usr/bin/env bash
# grok-journal-read-reminder — weekly Sat-noon PT email with last week's entry links
set -euo pipefail
REPO="${AGENT_REPO:-/root/astra-config}"
exec bash "$REPO/scripts/grok-journal-read-reminder.sh" "$REPO"
