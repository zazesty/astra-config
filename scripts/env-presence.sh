#!/usr/bin/env bash
# Safe env presence check — set|empty|absent|MISSING_FILE only. Never prints values.
# Installed to ~/.local/state/astra/env-presence.sh by setup or manual copy.
set -euo pipefail
presence() {
  local file="$1" key="$2"
  if [ ! -f "$file" ]; then echo "$file  $key  MISSING_FILE"; return; fi
  if grep -qE "^${key}=" "$file" 2>/dev/null; then
    val=$(grep -E "^${key}=" "$file" | head -1 | cut -d= -f2-)
    if [ -n "$val" ]; then echo "$file  $key  set"; else echo "$file  $key  empty"; fi
  else
    echo "$file  $key  absent"
  fi
}
file_presence() {
  local f="$1"
  if [ -f "$f" ]; then
    sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$sz" -gt 0 ]; then echo "$f  FILE  non-empty"; else echo "$f  FILE  empty"; fi
  else echo "$f  FILE  missing"; fi
}
echo "# env presence $(date -u +%Y-%m-%dT%H:%M:%SZ)"
for k in XAI_API_KEY GEMINI_API_KEY GEMINI_TRANSPORT OPENROUTER_API_KEY MCP_PATH RESEND_API_KEY NOTIFY_EMAIL_TO NOTIFY_EMAIL_FROM; do
  presence /etc/grok-mcp.env "$k"
done
for k in PLAID_CLIENT_ID PLAID_SECRET PLAID_SECRET_SANDBOX PLAID_ENV \
  PLAID_WEBHOOK_SECRET \
  PUSHOVER_TOKEN PUSHOVER_USER \
  TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_FROM TWILIO_TO; do
  presence /etc/hermes-finance.env "$k"
done
file_presence /root/.config/journal-trigger/endpoint
file_presence /root/.config/journal-trigger/secret
file_presence /root/.git-credentials
file_presence /root/.new-mcp-url
file_presence /root/.local/state/hermes-finance/config.json
echo "# full map: $HOME/.local/state/astra/env-map.md (private, not git)"
