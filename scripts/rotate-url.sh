#!/usr/bin/env bash
#
# Rotate MCP_PATH — the mount path is the endpoint's only secret, so we rotate it
# on purpose to bust a connector's per-URL tool cache or after a possible leak.
#
# This automates the BOX side end-to-end: generate a fresh path, back up + rewrite
# /etc/grok-mcp.env, restart the service, and smoke-test through the public Funnel.
# The three CLOUD consumers still need a manual reconnect — the script prints that
# checklist and the new URL at the end (the URL is a secret: it is never written to
# git and never echoed into a tracked file).
#
# Usage:  sudo bash scripts/rotate-url.sh [version-suffix]
#   The /vN suffix marks the TOOL-SURFACE version, NOT a rotation count. A pure
#   cache-bust rotation only needs a fresh random hex segment, so /vN is PRESERVED
#   by default. Pass a new tag (e.g. "/v12") ONLY when the tool surface actually
#   changed. Env knobs pass through to smoke-test.sh (EXPECTED_TOOLS, RETRIES…).
#
set -euo pipefail

ENV=/etc/grok-mcp.env
SERVICE=grok-mcp.service
HERE="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "rotate-url: run as root (needs to edit $ENV + restart $SERVICE)"; exit 1; }
[ -r "$ENV" ] || { echo "rotate-url: $ENV not readable"; exit 1; }
command -v openssl >/dev/null || { echo "rotate-url: openssl required"; exit 1; }

OLD="$(grep -E '^MCP_PATH=' "$ENV" | head -n1 | cut -d= -f2- | cut -d, -f1)"
[ -n "$OLD" ] || { echo "rotate-url: no MCP_PATH in $ENV"; exit 1; }

# /vN marks the tool-surface version, not a rotation count — PRESERVE it by default
# (the fresh random hex below is what busts caches). Pass $1 only on a surface change.
CUR_TAG="$(printf '%s' "$OLD" | grep -oE '/v[0-9]+$' || true)"
if [ -n "${1:-}" ]; then
  NEW_TAG="$1"
else
  NEW_TAG="$CUR_TAG"   # empty if the old path had no /vN suffix
fi

NEW="/mcp/v$(openssl rand -hex 4)-$(openssl rand -hex 4)${NEW_TAG}"

echo "rotate-url: $(printf '%s' "$OLD" | sed 's/[a-z0-9]/x/g') -> $(printf '%s' "$NEW" | sed 's/[a-z0-9]/x/g')"

# Back up the env (root-only) and replace ONLY the MCP_PATH line, preserving the
# rest of the file + mode 600.
BACKUP="${ENV}.bak.$(date +%Y%m%dT%H%M%S)"
cp -a "$ENV" "$BACKUP"
python3 - "$ENV" "$NEW" <<'PY'
import sys, re
env, new = sys.argv[1], sys.argv[2]
s = open(env).read()
s2 = re.sub(r'^MCP_PATH=.*$', f'MCP_PATH={new}', s, count=1, flags=re.M)
if s2 == s or f'MCP_PATH={new}' not in s2:
    sys.exit("rotate-url: failed to replace MCP_PATH line")
open(env, 'w').write(s2)
PY
chmod 600 "$ENV"

echo "rotate-url: restarting $SERVICE"
systemctl restart "$SERVICE"

# Wait for the new mount to answer on loopback before smoke-testing the Funnel.
for _ in $(seq 1 10); do
  sleep 1
  if curl -s --max-time 5 -o /dev/null "http://127.0.0.1:3000${NEW}" -X POST \
       -H 'Content-Type: application/json' \
       -H 'Accept: application/json, text/event-stream' \
       -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'; then
    break
  fi
done

# End-to-end through the public Funnel (smoke-test discovers the new path from $ENV).
if ! bash "$HERE/smoke-test.sh"; then
  echo
  echo "rotate-url: SMOKE-TEST FAILED. To roll back:"
  echo "  sudo cp -a '$BACKUP' '$ENV' && sudo systemctl restart $SERVICE"
  exit 1
fi

BASE="$(tailscale funnel status | grep -oE 'https://[^ ]+' | head -n1)"
cat <<EOF

rotate-url: DONE. New endpoint (secret — do not paste into git/chat logs you share):

  ${BASE}${NEW}

Reconnect ALL consumers to the new URL (none of these auto-update):
  1) claude.ai interactive connector (Astra)   — reconnect to new URL
  2) Grok connector                            — re-add with new URL (busts its per-URL cache)
  3) Claude Code journaling routine connector  — update URL (fails SILENTLY if missed)
  ( ~/.claude/settings.local.json curl allowlist entries, if any, are cosmetic. )

Old env backed up at: $BACKUP
EOF
