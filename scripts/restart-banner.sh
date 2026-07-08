#!/usr/bin/env bash
# SessionStart hook — surfaces grok-mcp restart alert into Claude Code context.
# No-op when clean. Always exit 0.
ALERT="${GROK_RESTART_ALERT:-/root/.grok-mcp-restart.alert}"
[ -f "$ALERT" ] && cat "$ALERT"
exit 0
