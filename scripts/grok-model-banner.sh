#!/usr/bin/env bash
# SessionStart hook — surfaces the weekly grok-model-check alert into the Claude
# Code session. A SessionStart hook's stdout enters Claude's context (same path
# as commit-if-changed's line), so the assistant sees a pending model change and
# can proactively offer to bump DEFAULT_MODEL. No-op (silent) when no alert is
# pending. Always exits 0 so it can never block session start.
#
# NOTE: deliberately Claude-facing, UNLIKE warn-uncommitted.sh (which is
# bashrc/interactive-only and kept OUT of Claude's context) — here we WANT the
# assistant in the loop to help action the bump.
ALERT="${GROK_MODEL_ALERT:-/root/.grok-model.alert}"
[ -f "$ALERT" ] && cat "$ALERT"
exit 0
