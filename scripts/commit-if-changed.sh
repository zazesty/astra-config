#!/usr/bin/env bash
# Commit the astra-config repo only if something changed. Used by the SessionStart
# Claude hook and the nightly user systemd timer. Commit-only (no push) — pushing
# is left to a deliberate manual step with the operator's token.
set -euo pipefail

REPO="${1:-/root/astra-config}"
cd "$REPO"

if [ -z "$(git status --porcelain)" ]; then
  echo "commit-if-changed: no changes in $REPO"
  exit 0
fi

git add -A
git commit -q -m "auto: config snapshot $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "commit-if-changed: committed snapshot in $REPO"
