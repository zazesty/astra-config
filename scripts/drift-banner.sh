#!/usr/bin/env bash
# SessionStart hook — surfaces the daily drift-check alert into the Claude Code
# session. A SessionStart hook's stdout enters Claude's context (same path as
# commit-if-changed + grok-model-banner), so the assistant sees unreproduced box
# state and can offer to mirror it into astra-config. No-op (silent) when clean.
# Always exits 0 so it can never block session start.
#
# Deliberately Claude-facing — config completeness is the assistant's job. This
# is the opposite stance from warn-uncommitted.sh (bashrc/interactive-only, kept
# OUT of Claude's context), which owns grok-mcp manual-backup hygiene.
ALERT="${ASTRA_DRIFT_ALERT:-/root/.astra-drift.alert}"
[ -f "$ALERT" ] && cat "$ALERT"
exit 0
