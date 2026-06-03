#!/usr/bin/env bash
# Push astra-config to origin IFF local is ahead. Runs from the nightly
# astra-commit.service (after commit-if-changed.sh), giving an off-box backup
# floor of 24h. Auth uses the stored github.com token (credential.helper=store,
# user zazesty) so it runs unattended.
#
# Deliberately SEPARATE from commit-if-changed.sh: the SessionStart Claude hook
# shares that script and must stay commit-only (Option A = only the nightly path
# pushes). Checking "ahead" independently also means a night whose push failed
# (offline) is retried the next night — accumulated commits go out together.
set -euo pipefail

REPO="${1:-/root/astra-config}"
cd "$REPO"

BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo main)"
git fetch -q origin "$BRANCH" 2>/dev/null || true   # refresh origin/BRANCH; tolerate offline

if [ -z "$(git rev-list "origin/$BRANCH..$BRANCH" 2>/dev/null)" ]; then
  echo "push-if-ahead: nothing to push ($BRANCH already at origin)"
  exit 0
fi

git push -q origin "$BRANCH"
echo "push-if-ahead: pushed $BRANCH to origin"
