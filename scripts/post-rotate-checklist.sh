#!/usr/bin/env bash
# =============================================================================
# post-rotate-checklist.sh — human checklist after MCP_PATH rotation (or when
# a consumer looks dead). Does NOT touch secrets; does NOT re-add connectors.
#
# How it shows up:
#   • Manual:  bash /root/astra-config/scripts/post-rotate-checklist.sh
#   • After rotate-url.sh (hooked below if present)
#   • SessionStart / agent: print this script's stdout into the chat (no popup UI)
#
# Claude Code and Grok Build do not get OS notifications from this — agents
# (or you) run it and paste/read the checklist in the conversation.
# =============================================================================
set -uo pipefail

echo "=== astra post-rotate / connector checklist ==="
echo "Time (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# Box path health (path redacted by smoke-test's own output filtering if any)
if [ -x /root/astra-config/scripts/smoke-test.sh ]; then
  echo "--- Box funnel smoke (EXPECTED_TOOLS auto) ---"
  if out="$(bash /root/astra-config/scripts/smoke-test.sh 2>&1)"; then
    echo "$out" | sed -E 's|https://[^ ]+|https://…/MCP_PATH|g' | grep -E 'smoke-test:|expecting' || echo "$out" | sed -E 's|https://[^ ]+|https://…/MCP_PATH|g' | tail -3
  else
    echo "$out" | sed -E 's|https://[^ ]+|https://…/MCP_PATH|g' | tail -5
    echo "SMOKE FAILED — fix box path before chasing cloud connectors."
  fi
else
  echo "(smoke-test.sh missing)"
fi
echo

if [ -x /root/astra-config/scripts/sync-grok-build-astra-mcp.sh ]; then
  echo "--- Grok Build loopback sync (local config only) ---"
  bash /root/astra-config/scripts/sync-grok-build-astra-mcp.sh 2>&1 | tail -5 || true
  echo "  → Restart the Grok Build *session* so tools reload."
else
  echo "(sync-grok-build-astra-mcp.sh missing — set [mcp_servers.astra] by hand)"
fi
echo

cat <<'EOF'
--- Human re-adds (cannot automate from the box) ---
[ ] Journaling routine MCP URL = current MCP_PATH (stale fails SILENTLY)
[ ] claude.ai project connector = same path (name can be "Astra V14"; name is cosmetic)
[ ] Grok (cloud) connector re-added if tool list stale (Grok caches tools PER URL)
[ ] Optional: remove old connector aliases (Astra V12/V13/…) to avoid picking a dead URL

Verify with a trivial tool call from each consumer (e.g. memory_list limit 1).
Funnel smoke PASS only proves the box — not that cloud UIs point at it.

Dismiss restart alert if present:  rm -f /root/.grok-mcp-restart.alert
EOF
