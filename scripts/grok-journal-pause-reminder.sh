#!/usr/bin/env bash
# grok-journal-pause-reminder.sh — weekly nudge: Grok journal API autopilot is
# paused until the owner unpauses (bank/budget). No-ops before 2026-07-19 PT.
# If ~/.config/grok-journal/enabled exists, send a one-line "already enabled"
# note only once is unnecessary — skip email entirely when enabled.
set -uo pipefail

DRY_RUN=0
FORCE=0
REPO="/root/astra-config"
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    -*)
      echo "unknown arg: $a" >&2
      exit 2
      ;;
    *)
      REPO="$a"
      ;;
  esac
done

NOTIFY="$REPO/scripts/notify-email.sh"
STATE_DIR="${HOME}/.config/grok-journal"
ENABLED_FLAG="$STATE_DIR/enabled"
REMINDER_FLAG="$STATE_DIR/pause-reminder-enabled"
TZPT=America/Los_Angeles
# First allowed fire: 2026-07-19 12:00 PT (afternoon window starts).
START_EPOCH=$(TZ="$TZPT" date -d "2026-07-19 12:00:00" +%s)
NOW_EPOCH=$(date +%s)

if [ "$DRY_RUN" != 1 ] && [ "$FORCE" != 1 ] && [ ! -f "$REMINDER_FLAG" ]; then
  echo "grok-journal-pause-reminder: disabled (touch $REMINDER_FLAG or pass --force)"
  exit 0
fi

if [ "$NOW_EPOCH" -lt "$START_EPOCH" ]; then
  echo "grok-journal-pause-reminder: before 2026-07-19 PT — skip"
  exit 0
fi

if [ -f "$ENABLED_FLAG" ]; then
  echo "grok-journal-pause-reminder: autopilot enabled — no reminder"
  exit 0
fi

BODY=$(cat <<EOF
Grok journal API autopilot is still PAUSED (cost hold).

When your bank account is more flush and you want nightly entries again:
  1. Approve pilot entry 001 (if not already)
  2. touch ~/.config/grok-journal/enabled
  3. Enable the grok-journal timer (when wired)
  4. Or just reply in Grok Build: "unpause grok journal autopilot"

Repo: https://github.com/zazesty/Grok-Journal
Plan: astra-config/docs/specs/grok_journal_plan.md

This reminder is weekly until you unpause (or delete this timer).
Box time: $(TZ=$TZPT date)
EOF
)

if [ "$DRY_RUN" = 1 ]; then
  printf '%s\n' "$BODY"
  exit 0
fi

printf '%s\n' "$BODY" | bash "$NOTIFY" "Grok journal autopilot still paused — unpause when flush?"
echo "grok-journal-pause-reminder: emailed"
exit 0
