#!/usr/bin/env bash
# =============================================================================
# smoke-test.sh :: end-to-end self-check for the grok-mcp funnel.
# Curls the PUBLIC funnel URL, calls tools/list, and asserts the expected tool
# count. setup.sh runs this as its final step so a rebuild verifies itself;
# also runnable anytime:  sudo bash scripts/smoke-test.sh
#
# Timing: after a fresh `tailscale up`, Funnel can take a few seconds to come
# live (cert + edge propagation), so we retry with backoff before failing.
# =============================================================================
set -euo pipefail

EXPECTED_TOOLS="${EXPECTED_TOOLS:-3}"
# Mount path comes from the off-repo env file (MCP_PATH); never hardcode it
# here. First MCP_PATH entry is used.
SECRET_ENV="${GROK_ENV:-/etc/grok-mcp.env}"
if [ -z "${MCP_PATH:-}" ] && [ -r "$SECRET_ENV" ]; then
  MCP_PATH="$(grep -E '^MCP_PATH=' "$SECRET_ENV" | head -n1 | cut -d= -f2- | cut -d, -f1)"
fi
[ -n "${MCP_PATH:-}" ] || { echo "smoke-test: FAIL — MCP_PATH not set (need $SECRET_ENV or MCP_PATH env)"; exit 1; }
RETRIES="${RETRIES:-15}"           # ~15 tries
SLEEP_SECS="${SLEEP_SECS:-3}"      # x 3s = up to ~45s for Funnel to come live

# --- Resolve the public funnel base URL (works on any tailnet, not hardcoded) -
BASE="${FUNNEL_URL:-}"
if [ -z "$BASE" ]; then
  BASE="$(tailscale funnel status 2>/dev/null \
            | grep -oE 'https://[^ ]+' | head -n1)"
fi
[ -n "$BASE" ] || { echo "smoke-test: FAIL — could not determine funnel URL (is Funnel on?)"; exit 1; }
BASE="${BASE%/}"
ENDPOINT="$BASE$MCP_PATH"

# --- node is guaranteed by setup; use it to parse the SSE/JSON robustly -------
NODE="$(command -v node || true)"
[ -n "$NODE" ] || NODE="/root/.nvm/versions/node/v22.22.3/bin/node"
[ -x "$NODE" ] || { echo "smoke-test: FAIL — node not found for JSON parse"; exit 1; }

echo "smoke-test: probing $ENDPOINT (expecting $EXPECTED_TOOLS tools)"

req='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

for attempt in $(seq 1 "$RETRIES"); do
  raw="$(curl -s --max-time 15 "$ENDPOINT" -X POST \
            -H 'Content-Type: application/json' \
            -H 'Accept: application/json, text/event-stream' \
            -d "$req" 2>/dev/null || true)"

  # Response is an SSE frame: a `data: {json}` line. Strip the prefix, count
  # tools by name, and print them. Exits 0 only on an exact count match.
  result="$(printf '%s' "$raw" | "$NODE" -e '
    let s = ""; process.stdin.on("data", d => s += d).on("end", () => {
      const line = s.split(/\r?\n/).find(l => l.startsWith("data:"));
      const payload = line ? line.slice(5).trim() : s.trim();
      try {
        const tools = (JSON.parse(payload).result || {}).tools || [];
        console.log(tools.length + " " + tools.map(t => t.name).join(","));
      } catch { console.log("-1 parse-error"); }
    });' 2>/dev/null || echo "-1 node-error")"

  count="${result%% *}"
  names="${result#* }"

  if [ "$count" = "$EXPECTED_TOOLS" ]; then
    echo "smoke-test: PASS — $count tools live: $names"
    exit 0
  fi

  echo "smoke-test: attempt $attempt/$RETRIES — got '$count' tools (want $EXPECTED_TOOLS), retrying in ${SLEEP_SECS}s..."
  sleep "$SLEEP_SECS"
done

echo "smoke-test: FAIL — funnel did not serve $EXPECTED_TOOLS tools after $RETRIES tries"
echo "  endpoint : $ENDPOINT"
echo "  last seen: ${count:-none} tools (${names:-})"
echo "  debug    : systemctl status grok-mcp.service ; tailscale funnel status"
exit 1
