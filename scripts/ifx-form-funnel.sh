#!/usr/bin/env bash
# Bind Tailscale Funnel secret path → local IFX form server (127.0.0.1:8767).
set -euo pipefail
# systemd oneshots often lack HOME
export HOME="${HOME:-/root}"
PORT="${IFX_FORM_PORT:-8767}"
STATE="${IFX_LOG_DIR:-$HOME/.local/state/health/ifx-cycle}"
CFG="$STATE/config.json"

# Ensure secret exists (server also does this; idempotent)
python3 - <<'PY'
from pathlib import Path
import json, secrets
from datetime import datetime, timezone
state = Path.home() / ".local/state" / "health" / "ifx-cycle"
state.mkdir(parents=True, exist_ok=True)
cfg_p = state / "config.json"
data = {}
if cfg_p.is_file():
    try:
        data = json.loads(cfg_p.read_text())
    except Exception:
        data = {}
if not data.get("form_secret"):
    data["form_secret"] = secrets.token_hex(16)
    data["form_secret_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg_p.write_text(json.dumps(data, indent=2) + "\n")
    cfg_p.chmod(0o600)
print(data["form_secret"])
PY

SECRET="$(python3 -c "import json; from pathlib import Path; print(json.loads(Path('$CFG').read_text())['form_secret'])")"
MOUNT="/ifx-log-${SECRET}"

for cmd in serve funnel; do
  tailscale "$cmd" --bg --yes --set-path="$MOUNT" "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true
done
echo "funnel mount=$MOUNT -> 127.0.0.1:${PORT}"
