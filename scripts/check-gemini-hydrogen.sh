#!/usr/bin/env bash
# check-gemini-hydrogen.sh — check if Gemini Hydrogen or Gemini 4 Flash is live on API.
# Sends one alert email via notify-email.sh upon detection, then silences itself.
set -euo pipefail

STATE_FILE="/root/.local/state/astra/gemini-hydrogen-notified"
NOTIFY_EMAIL="/root/astra-config/scripts/notify-email.sh"

if [ -f "$STATE_FILE" ]; then
  exit 0
fi

python3 - <<'PY'
import json, os, sys, urllib.request

env_file = "/etc/grok-mcp.env"
gemini_key = os.environ.get("GEMINI_API_KEY")
if not gemini_key and os.path.exists(env_file):
    try:
        with open(env_file) as f:
            for line in f:
                if line.startswith("GEMINI_API_KEY="):
                    gemini_key = line.strip().split("=", 1)[1].strip("\"'")
    except (PermissionError, OSError):
        pass

found = []

# 1. Check Google AI Studio direct
if gemini_key:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            for m in data.get("models", []):
                name = m.get("name", "")
                if "hydrogen" in name.lower() or "gemini-4-flash" in name.lower():
                    found.append(f"Google AI Studio: {name}")
    except Exception:
        pass

# 2. Check OpenRouter
try:
    req = urllib.request.Request("https://openrouter.ai/api/v1/models", headers={"User-Agent": "astra-box"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
        for m in data.get("data", []):
            mid = m.get("id", "")
            mname = m.get("name", "")
            if "hydrogen" in mid.lower() or "hydrogen" in mname.lower() or "gemini-4-flash" in mid.lower():
                found.append(f"OpenRouter: {mid} ({mname})")
except Exception:
    pass

if found:
    with open("/tmp/.hydrogen-found", "w") as f:
        f.write("\n".join(found))
sys.exit(0)
PY

if [ -f /tmp/.hydrogen-found ]; then
  BODY=$(cat /tmp/.hydrogen-found)
  rm -f /tmp/.hydrogen-found
  echo -e "Gemini Hydrogen / Gemini 4 Flash detected on API:\n\n$BODY\n\nReady to test or update Flash pin in grok-mcp / subagents." | "$NOTIFY_EMAIL" "[Model Alert] Gemini Hydrogen is live on API!"
  mkdir -p "$(dirname "$STATE_FILE")"
  touch "$STATE_FILE"
fi
