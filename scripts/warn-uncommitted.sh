#!/usr/bin/env bash
# Login warn net — sourced from ~/.bashrc for INTERACTIVE shells only, so it
# prints to the operator's terminal and NEVER into Claude's context. Surfaces
# the two backup gaps the automation can't fix on its own:
#   1. grok-mcp has uncommitted work (it is manual / not auto-backed-up).
#   2. astra-config's nightly auto-push failed (off-box backup is stale).

APP_REPO="/root/grok-mcp"
if [ -d "$APP_REPO/.git" ] && [ -n "$(git -C "$APP_REPO" status --porcelain)" ]; then
  echo "⚠️  grok-mcp has uncommitted changes — NOT backed up. Commit + push via its own flow."
fi

# Dropped by scripts/push-if-ahead.sh when the nightly push fails; removed on success.
PUSH_FAIL="${ASTRA_PUSH_FAIL:-/root/.astra-push.failed}"
if [ -f "$PUSH_FAIL" ]; then
  echo "⚠️  astra-config nightly auto-push FAILED — off-box backup is STALE:"
  sed 's/^/    /' "$PUSH_FAIL"
fi
