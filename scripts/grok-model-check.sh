#!/usr/bin/env bash
# =============================================================================
# grok-model-check.sh — weekly "is there a newer Grok?" freshness check.
#
# xAI exposes NO moving "-latest" alias (unlike Gemini's gemini-pro-latest), and
# its version strings are NOT reliably orderable (the operator confirmed 4.3 is
# newer than 4.20). So this script never guesses "best" / never auto-bumps. It
# only DETECTS change vs an acknowledged baseline and drops a sentinel that the
# SessionStart banner (grok-model-banner.sh) surfaces into the Claude session,
# where the operator + assistant decide whether to bump DEFAULT_MODEL.
#
# Fires the alert when EITHER:
#   • a grok text model appears that isn't in the acknowledged baseline, or
#   • the currently-pinned DEFAULT_MODEL has vanished from the catalog (retired).
#
# Usage:
#   grok-model-check.sh [REPO]     # run the check (default REPO=/root/astra-config)
#   grok-model-check.sh --ack      # accept current catalog as baseline, clear alert
# =============================================================================
set -uo pipefail

REPO="/root/astra-config"
ACK=""
case "${1:-}" in
  --ack) ACK=1 ;;
  "" ) ;;
  * ) REPO="$1" ;;
esac

ENV_FILE="${GROK_ENV:-/etc/grok-mcp.env}"
APP_SRC="${GROK_APP_SRC:-/root/grok-mcp/src/index.ts}"
KNOWN="${GROK_MODELS_KNOWN:-$REPO/state/grok-models.known}"
ALERT="${GROK_MODEL_ALERT:-/root/.grok-model.alert}"
BASE="${XAI_BASE_URL:-https://api.x.ai/v1}"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- load XAI_API_KEY (sourced; file is chmod 600, never echoed) --------------
set -a; . "$ENV_FILE" 2>/dev/null || true; set +a
[ -n "${XAI_API_KEY:-}" ] || { echo "grok-model-check: no XAI_API_KEY in $ENV_FILE" >&2; exit 1; }

# --- fetch catalog -> sorted, de-duped grok *text* model ids ------------------
# Excludes non-LLM SKUs (imagine/image/video) and internal build models.
fetch_catalog() {
  curl -fsS "${BASE%/}/models" -H "Authorization: Bearer $XAI_API_KEY" \
    | python3 -c '
import sys, json
d = json.load(sys.stdin)
ids = [m.get("id","") for m in d.get("data", d.get("models", []))]
keep = [i for i in ids if i.startswith("grok-")
        and not any(x in i for x in ("imagine","image","video","build"))]
print("\n".join(sorted(set(keep))))'
}

CURRENT="$(fetch_catalog 2>/dev/null)"
[ -n "$CURRENT" ] || { echo "grok-model-check: catalog fetch failed/empty (offline? bad key?)" >&2; exit 1; }

mkdir -p "$(dirname "$KNOWN")"

# --- --ack: accept the current catalog as the baseline and stop nagging -------
if [ -n "$ACK" ]; then
  printf '%s\n' "$CURRENT" > "$KNOWN"
  rm -f "$ALERT"
  echo "grok-model-check: acknowledged — baseline set to current catalog, alert cleared."
  exit 0
fi

# First ever run: seed baseline silently (operator already knows today's lineup).
[ -f "$KNOWN" ] || printf '%s\n' "$CURRENT" > "$KNOWN"

PINNED="$(grep -oE 'DEFAULT_MODEL[[:space:]]*=[[:space:]]*"[^"]+"' "$APP_SRC" 2>/dev/null \
          | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"

NEW="$(comm -23 <(printf '%s\n' "$CURRENT") <(sort -u "$KNOWN"))"
PINNED_GONE=""
if [ -n "$PINNED" ] && ! printf '%s\n' "$CURRENT" | grep -qxF "$PINNED"; then
  PINNED_GONE=1
fi

if [ -z "$NEW" ] && [ -z "$PINNED_GONE" ]; then
  rm -f "$ALERT"
  echo "grok-model-check: no change (pinned=${PINNED:-unknown})"
  exit 0
fi

# --- change detected: write the sentinel the SessionStart banner reads --------
{
  echo "🤖 GROK MODEL CHECK — catalog changed (checked $(stamp))"
  echo "   Pinned now (grok-mcp DEFAULT_MODEL): ${PINNED:-unknown}"
  if [ -n "$NEW" ]; then
    echo "   NEW model(s) on xAI not seen before:"
    printf '%s\n' "$NEW" | sed 's/^/     • /'
  fi
  if [ -n "$PINNED_GONE" ]; then
    echo "   ⚠️  Pinned model '$PINNED' is GONE from the catalog — likely retired; bump soon."
  fi
  echo "   Current grok text catalog:"
  printf '%s\n' "$CURRENT" | sed 's/^/     - /'
  echo "   xAI version strings aren't reliably orderable — pick deliberately."
  echo "   Bump:    edit DEFAULT_MODEL in /root/grok-mcp/src/index.ts, npm run build, restart grok-mcp.service"
  echo "   Dismiss: bash $REPO/scripts/grok-model-check.sh --ack"
} > "$ALERT"

echo "grok-model-check: CHANGE detected -> $ALERT"
exit 0
