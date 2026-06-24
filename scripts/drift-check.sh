#!/usr/bin/env bash
# =============================================================================
# drift-check.sh — assert host zaz-astra is still reproducible from astra-config.
#
# Catches box state that a `setup.sh` rebuild would NOT reproduce, and drops a
# sentinel that the SessionStart banner (drift-banner.sh) surfaces into the
# Claude session — where config completeness is the assistant's job to fix.
# (This is exactly the gap that left the journaling trigger un-backed-up until
# it was wired in. Deliberately Claude-facing, UNLIKE warn-uncommitted.sh, which
# owns grok-mcp backup hygiene and is kept operator-facing on purpose.)
#
# Checks (config-completeness only — grok-mcp push state is NOT here by design):
#   1. Tracked symlinks still resolve into the repo (broken/repointed/missing).
#   2. Every enabled systemd user timer / custom system unit has a tracked file.
#   3. The live root crontab matches the repo-tracked crontab.txt.
#   4. Every live /etc/grok-mcp.env KEY exists in .env.example (never values).
#
# Usage:
#   drift-check.sh [REPO]   # run the check (default REPO=/root/astra-config)
#   drift-check.sh --ack    # accept current drift as intentional, clear the alert
# =============================================================================
set -uo pipefail

REPO="/root/astra-config"
ACK=""
case "${1:-}" in
  --ack) ACK=1 ;;
  "" ) ;;
  * ) REPO="$1" ;;
esac

ALERT="${ASTRA_DRIFT_ALERT:-/root/.astra-drift.alert}"
ACKED="${ASTRA_DRIFT_ACKED:-$REPO/state/drift.acked}"
ENV_FILE="${GROK_ENV:-/etc/grok-mcp.env}"
CRONTAB_TRACKED="$REPO/home/journal-trigger/crontab.txt"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

findings=()

# --- check 1: tracked symlinks resolve into the repo -------------------------
# This list mirrors the `ln -sfnT` set in setup.sh step 5. Checks 2-4 below are
# the dynamic safety net that catches NEW box state even if this list lags.
symlinks=(
  "/etc/systemd/system/grok-mcp.service|etc/systemd/system/grok-mcp.service"
  "/etc/sysctl.d/99-swap.conf|etc/sysctl.d/99-swap.conf"
  "/root/.claude/settings.json|home/.claude/settings.json"
  "/root/.bashrc|home/.bashrc"
  "/root/journal-trigger/usage-gate.sh|home/journal-trigger/usage-gate.sh"
  "/root/journal-trigger/journal-trigger.sh|home/journal-trigger/journal-trigger.sh"
)
for u in astra-commit grok-model-check gemini-model-check health-check drift-check; do
  for k in service timer; do
    symlinks+=("/root/.config/systemd/user/$u.$k|home/.config/systemd/user/$u.$k")
  done
done
for pair in "${symlinks[@]}"; do
  live="${pair%%|*}"; rel="${pair##*|}"
  exp="$(readlink -f "$REPO/$rel" 2>/dev/null)"
  got="$(readlink -f "$live" 2>/dev/null)"
  if [ -z "$exp" ]; then
    findings+=("symlink: repo source missing -> $rel")
  elif [ "$got" != "$exp" ]; then
    findings+=("symlink: $live -> ${got:-MISSING} (expected $exp)")
  fi
done

# --- check 2: enabled units have tracked unit files --------------------------
while read -r t; do
  [ -n "$t" ] || continue
  [ -f "$REPO/home/.config/systemd/user/$t" ] \
    || findings+=("unit: enabled user timer '$t' has no tracked file in the repo")
done < <(systemctl --user list-timers --all --no-legend 2>/dev/null \
           | grep -oE '[A-Za-z0-9_.@-]+\.timer' | sort -u)

while read -r u; do
  [ -n "$u" ] || continue
  [ -f "$REPO/etc/systemd/system/$u" ] \
    || findings+=("unit: enabled custom system unit '$u' has no tracked file in the repo")
done < <(systemctl list-unit-files --state=enabled --no-legend 2>/dev/null \
           | grep -oiE '(grok|astra|journal)[A-Za-z0-9_.@-]*\.(service|timer)' | sort -u)

# --- check 3: live root crontab == tracked crontab.txt -----------------------
if [ -f "$CRONTAB_TRACKED" ]; then
  if ! diff -q <(crontab -l 2>/dev/null) "$CRONTAB_TRACKED" >/dev/null 2>&1; then
    findings+=("crontab: live root crontab differs from tracked home/journal-trigger/crontab.txt")
  fi
else
  findings+=("crontab: tracked crontab.txt is missing from the repo")
fi

# --- check 4: live env KEYS all present in the template (names only) ----------
keys() { grep -oE '^[A-Z_]+=' "$1" 2>/dev/null | tr -d '=' | sort -u; }
if [ -r "$ENV_FILE" ]; then
  while read -r k; do
    [ -n "$k" ] && findings+=("env: live key '$k' is not in .env.example (rebuild scaffold would miss it)")
  done < <(comm -23 <(keys "$ENV_FILE") <(keys "$REPO/.env.example"))
fi

# --- ack / alert bookkeeping -------------------------------------------------
fingerprint() { printf '%s\n' "${findings[@]}" | sort | sha256sum | cut -d' ' -f1; }

if [ -n "$ACK" ]; then
  if [ "${#findings[@]}" -eq 0 ]; then
    rm -f "$ALERT" "$ACKED"; echo "drift-check: nothing to ack (already clean)."; exit 0
  fi
  mkdir -p "$(dirname "$ACKED")"; fingerprint > "$ACKED"; rm -f "$ALERT"
  echo "drift-check: acknowledged ${#findings[@]} item(s) as intentional; alert cleared."
  exit 0
fi

if [ "${#findings[@]}" -eq 0 ]; then
  rm -f "$ALERT" "$ACKED"; echo "drift-check: CLEAN — box reproduces from $REPO"; exit 0
fi

if [ -f "$ACKED" ] && [ "$(fingerprint)" = "$(cat "$ACKED" 2>/dev/null)" ]; then
  rm -f "$ALERT"
  echo "drift-check: ${#findings[@]} item(s) drift, all match the acked baseline — suppressed."
  exit 0
fi

{
  echo "🧭 DRIFT CHECK — box state not reflected in the astra-config rebuild (checked $(stamp))"
  printf '%s\n' "${findings[@]}" | sed 's/^/   • /'
  echo "   Fix: mirror each item into $REPO (symlink / unit / crontab.txt / .env.example), then commit."
  echo "   Intentional & not-yet-mirrored? Dismiss: bash $REPO/scripts/drift-check.sh --ack"
} > "$ALERT"
rm -f "$ACKED"   # findings changed since any prior ack
echo "drift-check: DRIFT detected (${#findings[@]} item(s)) -> $ALERT"
exit 0
