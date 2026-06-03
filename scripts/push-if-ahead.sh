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
#
# Failure is NEVER silent: every run appends to LOG, and a failure drops a FAIL
# sentinel that the ~/.bashrc login warn (warn-uncommitted.sh) surfaces to the
# operator's terminal. A clean push removes the sentinel.
set -uo pipefail

REPO="${1:-/root/astra-config}"
LOG="${ASTRA_PUSH_LOG:-/root/.astra-push.log}"
FAIL="${ASTRA_PUSH_FAIL:-/root/.astra-push.failed}"
cd "$REPO" || { echo "push-if-ahead: repo $REPO not found" >&2; exit 1; }

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log()   { printf '%s %s\n' "$(stamp)" "$*" >> "$LOG"; }

BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo main)"
git fetch -q origin "$BRANCH" 2>/dev/null || true   # refresh origin/BRANCH; tolerate offline

if [ -z "$(git rev-list "origin/$BRANCH..$BRANCH" 2>/dev/null)" ]; then
  log "OK   nothing to push ($BRANCH at origin)"
  rm -f "$FAIL"
  echo "push-if-ahead: nothing to push ($BRANCH already at origin)"
  exit 0
fi

if out="$(git push origin "$BRANCH" 2>&1)"; then
  log "OK   pushed $BRANCH -> origin"
  rm -f "$FAIL"
  echo "push-if-ahead: pushed $BRANCH to origin"
  exit 0
fi

# --- push failed: log it, drop the sentinel, exit non-zero (so the unit + journal
#     also reflect the failure). The login warn net reads $FAIL. ----------------
log "FAIL push $BRANCH -> origin :: ${out//$'\n'/ | }"
{
  echo "astra-config nightly auto-push FAILED at $(stamp)"
  echo "branch: $BRANCH  ->  origin"
  echo "git said:"
  printf '%s\n' "$out" | sed 's/^/  /'
  echo "full log: $LOG"
} > "$FAIL"
echo "push-if-ahead: FAILED to push $BRANCH (see $LOG ; sentinel $FAIL)" >&2
exit 1
