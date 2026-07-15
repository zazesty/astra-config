#!/usr/bin/env bash
# agent-run.sh — thin scheduled-agent runner.
# Usage: agent-run.sh <job-id> [--force]
#
# Convention:
#   Job body:     $REPO/agent-jobs/<job-id>.sh  (executable, sourced or exec'd)
#   Disable:      ~/.config/agent-jobs/<job-id>.disabled
#   Lock:         ~/.local/state/agent-jobs/<job-id>.lock
#   Log:          ~/.local/state/agent-jobs/<job-id>.log
#   Last status:  ~/.local/state/agent-jobs/<job-id>.last.json
#
# Env:
#   AGENT_TIMEOUT_SECS  default 300
#   AGENT_REPO          default /root/astra-config
set -uo pipefail

JOB_ID="${1:-}"
FORCE=0
shift || true
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
  esac
done

if [ -z "$JOB_ID" ] || [[ ! "$JOB_ID" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "usage: agent-run.sh <job-id> [--force]" >&2
  exit 2
fi

REPO="${AGENT_REPO:-/root/astra-config}"
JOB_SH="$REPO/agent-jobs/${JOB_ID}.sh"
STATE_DIR="${HOME}/.local/state/agent-jobs"
CFG_DIR="${HOME}/.config/agent-jobs"
LOCK="$STATE_DIR/${JOB_ID}.lock"
LOG="$STATE_DIR/${JOB_ID}.log"
LAST="$STATE_DIR/${JOB_ID}.last.json"
DISABLED="$CFG_DIR/${JOB_ID}.disabled"
TIMEOUT_SECS="${AGENT_TIMEOUT_SECS:-300}"
NOTIFY="$REPO/scripts/notify-email.sh"

mkdir -p "$STATE_DIR" "$CFG_DIR"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(stamp)" "$*" >>"$LOG"; }

if [ ! -f "$JOB_SH" ]; then
  echo "agent-run: missing job $JOB_SH" >&2
  exit 2
fi

if [ "$FORCE" != 1 ] && [ -f "$DISABLED" ]; then
  log "skip job=$JOB_ID reason=disabled"
  echo "agent-run: $JOB_ID disabled"
  exit 0
fi

# Serialize ticks
exec 9>"$LOCK"
if ! flock -n 9; then
  log "skip job=$JOB_ID reason=lock_busy"
  echo "agent-run: $JOB_ID lock busy"
  exit 0
fi

START_EPOCH=$(date +%s)
START_ISO=$(stamp)
log "start job=$JOB_ID timeout=${TIMEOUT_SECS}s"
set +e
timeout --signal=TERM --kill-after=15 "${TIMEOUT_SECS}" bash "$JOB_SH" >>"$LOG" 2>&1
RC=$?
set -e
END_ISO=$(stamp)
END_EPOCH=$(date +%s)
DUR=$((END_EPOCH - START_EPOCH))

# timeout -> 124
STATUS=ok
if [ "$RC" -ne 0 ]; then STATUS=fail; fi
if [ "$RC" -eq 124 ]; then STATUS=timeout; fi

python3 - "$LAST" "$JOB_ID" "$STATUS" "$RC" "$START_ISO" "$END_ISO" "$DUR" <<'PY'
import json, sys
path, job, status, rc, start, end, dur = sys.argv[1:]
obj = {
  "job_id": job,
  "status": status,
  "exit_code": int(rc),
  "started_at": start,
  "ended_at": end,
  "duration_secs": int(dur),
}
open(path, "w").write(json.dumps(obj, separators=(",", ":")) + "\n")
PY

log "end job=$JOB_ID status=$STATUS rc=$RC duration_s=$DUR"

# Generic fail email only if job didn't handle notify (opt-in).
# Most jobs email themselves with a specific subject/body.
if [ "$STATUS" != ok ] && [ "${AGENT_EMAIL_ON_FAIL:-0}" = 1 ] && [ -f "$NOTIFY" ]; then
  {
    echo "agent-run job failed: $JOB_ID"
    echo "status=$STATUS rc=$RC duration_s=$DUR"
    echo "log=$LOG"
    echo "last=$LAST"
    echo
    tail -n 40 "$LOG" 2>/dev/null || true
  } | bash "$NOTIFY" "agent-job FAIL: $JOB_ID" || true
fi

# oneshot jobs: status is in last.json; timers should stay green
exit 0
