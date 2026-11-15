#!/usr/bin/env bash
#
# Rotate MCP_PATH — the mount path is the endpoint's only secret, so we rotate it
# on purpose to bust a connector's per-URL tool cache or after a possible leak.
#
# Going forward (2026-11-14): the pinned mount stays live as a trailing
# comma-separated path in MCP_PATH (configured via MCP_PATH_PINNED in the env
# file) so Claude, GPT, and Gemini keep the same pinned URL (Claude/GPT refresh
# in place; Gemini auto-refreshes automatically). Only the primary rotating
# mount (first entry) rotates for Grok, plus on-box loopback/Open WebUI syncs.
#
# This automates the BOX side end-to-end: generate a fresh rotating path, keep
# any pinned path live, back up + rewrite /etc/grok-mcp.env, restart the
# service, and smoke-test through the public Funnel.
#
# Usage:  sudo bash scripts/rotate-url.sh [version-suffix]
#   Pass a tag (e.g. "/v12") to override auto-bump. Env knobs pass through to
#   smoke-test.sh (EXPECTED_TOOLS, RETRIES…).
#
set -euo pipefail

ENV=/etc/grok-mcp.env
SERVICE=grok-mcp.service
HERE="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "rotate-url: run as root (needs to edit $ENV + restart $SERVICE)"; exit 1; }
[ -r "$ENV" ] || { echo "rotate-url: $ENV not readable"; exit 1; }
command -v openssl >/dev/null || { echo "rotate-url: openssl required"; exit 1; }

RAW_PATHS="$(grep -E '^MCP_PATH=' "$ENV" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
RAW_PINNED="$(grep -E '^MCP_PATH_PINNED=' "$ENV" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
OLD="$(printf '%s' "$RAW_PATHS" | cut -d, -f1 | tr -d '[:space:]')"
[ -n "$OLD" ] || { echo "rotate-url: no MCP_PATH in $ENV"; exit 1; }

# Bump the trailing /vN each rotation (a loose, monotonic rotation tag) unless an
# explicit tag is passed as $1. The fresh random hex is what actually busts caches.
CUR_TAG="$(printf '%s' "$OLD" | grep -oE '/v[0-9]+$' || true)"
if [ -n "${1:-}" ]; then
  NEW_TAG="$1"
elif [ -n "$CUR_TAG" ]; then
  NEW_TAG="/v$(( ${CUR_TAG#/v} + 1 ))"
else
  NEW_TAG="/v1"
fi

# 12 hex chars (~48 bits) — shorter than the old 2-segment form, still a strong
# secret (the mount path is the only credential in front of the API keys).
NEW="/mcp/$(openssl rand -hex 6)${NEW_TAG}"

# Preserve existing trailing pinned mounts and any MCP_PATH_PINNED entries.
PINNED_CSV="$(python3 - "$RAW_PATHS" "$RAW_PINNED" "$NEW" <<'PY'
import sys
raw, raw_pinned, new_path = sys.argv[1], sys.argv[2], sys.argv[3]
parts = [p.strip() for p in raw.split(",") if p.strip()]
extra_pinned = [p.strip() for p in raw_pinned.split(",") if p.strip()]
pinned = []
for p in [*parts[1:], *extra_pinned]:
    if p != new_path and p not in pinned:
        pinned.append(p)
print(",".join(pinned))
PY
)"

if [ -n "$PINNED_CSV" ]; then
  FULL_MCP_PATH="${NEW},${PINNED_CSV}"
  PINNED_NOTE="pinned mount(s) kept"
else
  FULL_MCP_PATH="${NEW}"
  PINNED_NOTE="no pinned mount"
fi

echo "rotate-url: $(printf '%s' "$OLD" | sed 's/[a-z0-9]/x/g') -> $(printf '%s' "$NEW" | sed 's/[a-z0-9]/x/g') ($PINNED_NOTE)"

# Back up the env (root-only) and replace ONLY the MCP_PATH line, preserving the
# rest of the file + mode 600.
BACKUP="${ENV}.bak.$(date +%Y%m%dT%H%M%S)"
cp -a "$ENV" "$BACKUP"
python3 - "$ENV" "$FULL_MCP_PATH" <<'PY'
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

# End-to-end through the public Funnel (smoke-test discovers the primary path from $ENV).
if ! bash "$HERE/smoke-test.sh"; then
  echo
  echo "rotate-url: SMOKE-TEST FAILED. To roll back:"
  echo "  sudo cp -a '$BACKUP' '$ENV' && sudo systemctl restart $SERVICE"
  exit 1
fi

# Also confirm any pinned mount(s) (for Claude/GPT) still serve tools/list.
if [ -n "$PINNED_CSV" ]; then
  IFS=',' read -r -a PINNED_ARR <<<"$PINNED_CSV"
  for p in "${PINNED_ARR[@]}"; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:3000${p}" -X POST \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' || echo 000)"
    if [ "$code" != "200" ]; then
      echo "rotate-url: WARN pinned mount returned HTTP $code" >&2
    fi
  done
  echo "rotate-url: pinned ${#PINNED_ARR[@]} mount(s) verified live on loopback"
fi

# Keep Grok Build loopback MCP pointed at the new rotating path (local ~/.grok only).
if [ -x "$HERE/sync-grok-build-astra-mcp.sh" ]; then
  bash "$HERE/sync-grok-build-astra-mcp.sh" || echo "rotate-url: WARN sync-grok-build-astra-mcp failed" >&2
fi
# Hermes keeps its own copy of the loopback URL. A restart is what reloads it;
# photon-heal syncs again before it restarts the gateway.
if [ -x "$HERE/sync-hermes-astra-mcp.sh" ]; then
  bash "$HERE/sync-hermes-astra-mcp.sh" || echo "rotate-url: WARN sync-hermes-astra-mcp failed" >&2
fi
# Open WebUI stores its own MCP connector URL; update that existing entry via
# its authenticated admin API so it follows rotations without a manual edit.
if [ -f "$HERE/sync-open-webui-astra-mcp.py" ]; then
  python3 "$HERE/sync-open-webui-astra-mcp.py" || echo "rotate-url: WARN sync-open-webui-astra-mcp failed" >&2
fi

BASE="$(tailscale funnel status | grep -oE 'https://[^ ]+' | head -n1)"
cat <<EOF

rotate-url: DONE. New rotating endpoint for Grok (secret — do not paste into git/chat logs you share):

  ${BASE}${NEW}

Pinned URL stays live for Claude, GPT, and Gemini (refresh the same pinned URL in Claude and GPT; Gemini auto-refreshes).
Old env backed up at: $BACKUP
EOF

# Human checklist (smoke already ran; sync already ran). Path-redacted; no secrets.
if [ -x "$HERE/post-rotate-checklist.sh" ]; then
  echo
  bash "$HERE/post-rotate-checklist.sh" || true
fi
