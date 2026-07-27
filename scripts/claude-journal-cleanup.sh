#!/usr/bin/env bash
# =============================================================================
# claude-journal-cleanup.sh — phased retirement of the Claude /fire journal cron
#
# Phases (America/Los_Angeles dates, inclusive):
#   archive  — on/after 2026-08-16: stop live firing surface (crontab + oauth-watch)
#   delete   — on/after 2026-08-30: purge local secrets + runtime state
#
# Does NOT delete astra-config source (home/journal-trigger/*) — git is the
# permanent archive. Re-run is idempotent. --force-phase=archive|delete for admin.
# =============================================================================
set -euo pipefail

REPO="${1:-/root/astra-config}"
NOTIFY="$REPO/scripts/notify-email.sh"
TZPT=America/Los_Angeles
STATE_DIR="${HOME}/.local/state/claude-journal-cleanup"
LOG="$STATE_DIR/cleanup.log"
ARCHIVE_MARKER="$STATE_DIR/archived-on"
DELETE_MARKER="$STATE_DIR/deleted-on"
ARCHIVE_ON="2026-08-16"   # PT calendar day
DELETE_ON="2026-08-30"

FORCE_PHASE=""
for a in "${@:2}"; do
  case "$a" in
    --force-phase=archive) FORCE_PHASE=archive ;;
    --force-phase=delete)  FORCE_PHASE=delete ;;
    --dry-run) DRY_RUN=1 ;;
  esac
done
DRY_RUN="${DRY_RUN:-0}"

mkdir -p "$STATE_DIR"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(stamp)" "$*" | tee -a "$LOG" >&2; }

TODAY_PT=$(TZ="$TZPT" date +%F)

phase_due() {
  # $1 = YYYY-MM-DD threshold; true if today >= threshold
  [[ "$TODAY_PT" > "$1" || "$TODAY_PT" == "$1" ]]
}

do_archive() {
  if [ -f "$ARCHIVE_MARKER" ] && [ -z "$FORCE_PHASE" ]; then
    log "archive: already done ($(cat "$ARCHIVE_MARKER"))"
    return 0
  fi
  log "archive: begin pt=$TODAY_PT"

  if [ "$DRY_RUN" = 1 ]; then
    log "archive: DRY_RUN — would remove journal crontab + disable oauth-watch"
    return 0
  fi

  # 1) Remove root crontab entries that call journal-trigger (leave empty crontab OK)
  if crontab -l >/tmp/.claude-journal-cron.bak 2>/dev/null; then
    if grep -q 'journal-trigger' /tmp/.claude-journal-cron.bak 2>/dev/null; then
      grep -v 'journal-trigger' /tmp/.claude-journal-cron.bak | crontab - || true
      log "archive: stripped journal-trigger lines from root crontab (bak=/tmp/.claude-journal-cron.bak)"
    else
      log "archive: no journal-trigger lines in crontab"
    fi
  else
    log "archive: no root crontab"
  fi

  # 2) Stop oauth-watch (only purpose was Claude journal gate keep-alive)
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  if systemctl --user disable --now journal-oauth-watch.timer 2>/dev/null; then
    log "archive: disabled journal-oauth-watch.timer"
  else
    log "archive: journal-oauth-watch.timer already off or missing"
  fi

  # 3) Note: scripts stay in astra-config; secrets stay until delete phase
  printf '%s\n' "$TODAY_PT" >"$ARCHIVE_MARKER"
  {
    echo "Claude journal live surface ARCHIVED on $TODAY_PT PT."
    echo
    echo "Done:"
    echo "  • root crontab lines invoking journal-trigger removed"
    echo "  • journal-oauth-watch.timer disabled"
    echo
    echo "Kept until delete phase ($DELETE_ON):"
    echo "  • ~/.config/journal-trigger/{secret,endpoint}"
    echo "  • ~/.local/state/journal-* logs"
    echo "  • astra-config/home/journal-trigger/* (git archive forever)"
    echo
    echo "Delete phase scheduled: $DELETE_ON PT (secrets + runtime state purge)."
    echo "Box: $(hostname) $(stamp)"
  } | bash "$NOTIFY" "Claude journal cron archived ($TODAY_PT)" || true

  log "archive: complete"
}

do_delete() {
  if [ ! -f "$ARCHIVE_MARKER" ] && [ -z "$FORCE_PHASE" ]; then
    log "delete: skip — archive phase not done yet"
    return 0
  fi
  if [ -f "$DELETE_MARKER" ] && [ -z "$FORCE_PHASE" ]; then
    log "delete: already done ($(cat "$DELETE_MARKER"))"
    return 0
  fi
  log "delete: begin pt=$TODAY_PT"

  if [ "$DRY_RUN" = 1 ]; then
    log "delete: DRY_RUN — would purge secrets + runtime state"
    return 0
  fi

  # Secrets
  for f in "${HOME}/.config/journal-trigger/secret" "${HOME}/.config/journal-trigger/endpoint"; do
    if [ -f "$f" ]; then
      # shred-ish: overwrite then remove
      : >"$f" 2>/dev/null || true
      rm -f "$f"
      log "delete: removed $f"
    fi
  done

  # Runtime state (logs optional keep — we archive logs into cleanup dir then drop originals)
  mkdir -p "$STATE_DIR/archived-state"
  for f in \
    "${HOME}/.local/state/journal-cron.log" \
    "${HOME}/.local/state/journal-fired-pt-date" \
    "${HOME}/.local/state/journal-oauth-last-refresh" \
    "${HOME}/.local/state/journal-trigger.lock"
  do
    if [ -e "$f" ]; then
      mv -f "$f" "$STATE_DIR/archived-state/" 2>/dev/null || rm -f "$f"
      log "delete: moved/removed $(basename "$f")"
    fi
  done

  # Ensure crontab still clean
  if crontab -l 2>/dev/null | grep -q 'journal-trigger'; then
    crontab -l | grep -v 'journal-trigger' | crontab - || true
    log "delete: re-stripped crontab"
  fi

  printf '%s\n' "$TODAY_PT" >"$DELETE_MARKER"
  {
    echo "Claude journal local secrets + runtime state DELETED on $TODAY_PT PT."
    echo
    echo "Removed: ~/.config/journal-trigger secrets, journal-* state files."
    echo "Logs snapshot (if any): $STATE_DIR/archived-state/"
    echo "Source scripts remain in astra-config (git) — not deleted."
    echo "Box: $(hostname) $(stamp)"
  } | bash "$NOTIFY" "Claude journal local state deleted ($TODAY_PT)" || true

  log "delete: complete"
}

# --- main ------------------------------------------------------------------
log "tick today_pt=$TODAY_PT force=${FORCE_PHASE:-none} dry_run=$DRY_RUN"

if [ "$FORCE_PHASE" = "archive" ]; then
  do_archive
elif [ "$FORCE_PHASE" = "delete" ]; then
  do_delete
else
  if phase_due "$ARCHIVE_ON"; then
    do_archive
  else
    log "archive: not due until $ARCHIVE_ON (today $TODAY_PT)"
  fi
  if phase_due "$DELETE_ON"; then
    do_delete
  else
    log "delete: not due until $DELETE_ON (today $TODAY_PT)"
  fi
fi

exit 0
