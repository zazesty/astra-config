#!/usr/bin/env bash
APP_REPO="/root/grok-mcp"
if [ -d "$APP_REPO/.git" ] && [ -n "$(git -C "$APP_REPO" status --porcelain)" ]; then
  echo "⚠️  grok-mcp has uncommitted changes — NOT backed up. Commit + push via its own flow."
fi
