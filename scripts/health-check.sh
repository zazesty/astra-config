#!/usr/bin/env bash
# =============================================================================
# health-check.sh — periodic liveness probe for the grok-mcp funnel.
# Reuses smoke-test.sh (curls the PUBLIC funnel, asserts the tool count) but for
# STEADY-STATE monitoring, so it retries only briefly (funnel is already warm).
#
# Anti-flap: a single failed probe does NOT email (transient edge/cert blips
# happen). It tracks consecutive failures in a counter file and emails via
# notify-email.sh only when the streak reaches FAILS_BEFORE_EMAIL (default 2),
# then again every EMAIL_EVERY failures (default 24) so a long outage nudges
# without spamming. A successful probe resets the streak and sends one
# "recovered" email if it had previously alerted.
#
# Wired to health-check.timer (hourly). Manual: bash scripts/health-check.sh
# =============================================================================
set -uo pipefail

REPO="${1:-/root/astra-config}"
COUNT_FILE="${HEALTH_COUNT:-/root/.astra-health.failcount}"
ALERTED="${HEALTH_ALERTED:-/root/.astra-health.alerted}"
FAILS_BEFORE_EMAIL="${FAILS_BEFORE_EMAIL:-2}"
EMAIL_EVERY="${EMAIL_EVERY:-24}"
NOTIFY="$REPO/scripts/notify-email.sh"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Steady-state probe: a couple of quick tries, not the ~45s setup-time backoff.
if out="$(RETRIES=2 SLEEP_SECS=4 bash "$REPO/scripts/smoke-test.sh" 2>&1)"; then
  # --- healthy: reset streak, send a recovery note if we'd alerted before -----
  prev="$(cat "$COUNT_FILE" 2>/dev/null || echo 0)"
  : > "$COUNT_FILE"; echo 0 > "$COUNT_FILE"
  if [ -f "$ALERTED" ]; then
    printf 'grok-mcp funnel is healthy again as of %s\n\n%s\n' "$(stamp)" "$out" \
      | "$NOTIFY" "✅ grok-mcp RECOVERED"
    rm -f "$ALERTED"
  fi
  echo "health-check: OK"
  exit 0
fi

# --- failed: bump the consecutive-failure streak ------------------------------
n="$(cat "$COUNT_FILE" 2>/dev/null || echo 0)"; n=$((n + 1)); echo "$n" > "$COUNT_FILE"
echo "health-check: FAIL (streak=$n)" >&2

# Email on the first crossing of the threshold, then every EMAIL_EVERY after.
if [ "$n" -ge "$FAILS_BEFORE_EMAIL" ] && { [ "$n" -eq "$FAILS_BEFORE_EMAIL" ] || [ $(( (n - FAILS_BEFORE_EMAIL) % EMAIL_EVERY )) -eq 0 ]; }; then
  {
    echo "grok-mcp funnel health check FAILED — $n consecutive probe(s), as of $(stamp)."
    echo
    echo "Probe output:"
    printf '%s\n' "$out" | sed 's/^/  /'
    echo
    echo "Check on the box:"
    echo "  systemctl status grok-mcp.service"
    echo "  tailscale funnel status"
    echo "  journalctl -u grok-mcp.service -n 50"
  } | "$NOTIFY" "🔴 grok-mcp DOWN (x$n)"
  touch "$ALERTED"
fi
exit 0
