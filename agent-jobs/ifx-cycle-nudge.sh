#!/usr/bin/env bash
# ifx-cycle-nudge — Mon/Thu morning PT email: log IFX symptoms (form link).
set -euo pipefail

NOTIFY="${IFX_NOTIFY_CMD:-/root/astra-config/scripts/notify-email.sh}"
STATE="${IFX_LOG_DIR:-$HOME/.local/state/health/ifx-cycle}"
HOST="${IFX_PUBLIC_HOST:-zaz-astra.tail5d74e1.ts.net}"

mkdir -p "$STATE"
chmod 700 "$STATE" 2>/dev/null || true

# Ensure form secret + URL
FORM_URL="$(python3 - <<PY
import json
from pathlib import Path
import secrets
state = Path("$STATE")
cfg_p = state / "config.json"
data = {}
if cfg_p.is_file():
    try:
        data = json.loads(cfg_p.read_text())
    except Exception:
        data = {}
if not data.get("form_secret"):
    data["form_secret"] = secrets.token_hex(16)
    cfg_p.write_text(json.dumps(data, indent=2) + "\n")
    cfg_p.chmod(0o600)
print(f"https://$HOST/ifx-log-{data['form_secret']}/")
PY
)"

STATUS="$(python3 - <<'PY'
import json
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo
state = Path.home() / ".local/state" / "health" / "ifx-cycle"
cfg = {}
if (state / "config.json").is_file():
    cfg = json.loads((state / "config.json").read_text())
inf = cfg.get("last_infusion_date") or "(not set)"
today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
days = None
if inf and inf != "(not set)":
    try:
        days = (today - date.fromisoformat(inf)).days
    except ValueError:
        days = None
# q8w ≈ 56 days; last ~10 days = end-of-cycle window
window = ""
if days is not None:
    if days >= 46:
        window = "END-OF-CYCLE window (last ~10d of q8w) — denser logging helps."
    elif days >= 35:
        window = "Approaching end-of-cycle — keep 2–3×/week."
    else:
        window = "Mid-cycle — 2×/week is enough unless symptoms appear."
rows = 0
jsonl = state / "log.jsonl"
if jsonl.is_file():
    rows = sum(1 for ln in jsonl.read_text().splitlines() if ln.strip())
print(f"today_pt={today.isoformat()}")
print(f"last_infusion={inf}")
print(f"days_since={days if days is not None else 'unknown'}")
print(f"log_rows={rows}")
print(f"window={window}")
PY
)"

TODAY_PT="$(echo "$STATUS" | sed -n 's/^today_pt=//p')"
INF="$(echo "$STATUS" | sed -n 's/^last_infusion=//p')"
DAYS="$(echo "$STATUS" | sed -n 's/^days_since=//p')"
ROWS="$(echo "$STATUS" | sed -n 's/^log_rows=//p')"
WINDOW="$(echo "$STATUS" | sed -n 's/^window=//p')"

SUBJECT="IFX log · day +${DAYS} · ${TODAY_PT}"

BODY="$(cat <<EOF
IFX cycle check-in (Mon/Thu morning)

Date (PT):     ${TODAY_PT}
Last infusion: ${INF}
Days since:    +${DAYS}
Log rows so far: ${ROWS}
${WINDOW}

Form (~30s, auto-saves on box):
${FORM_URL}

Fields: sleep 1–5 · energy/floaty 1–5 · stool 2–6 · cramp · BM (default 1) · notes

Why: track end-of-cycle sleep/energy/floaty + stool for a data-driven GI talk about
possible interval shortening (q8w → q6–7w). Deep remission context only.

— zaz-astra · ifx-cycle-nudge.timer
EOF
)"

printf '%s\n' "$BODY" | "$NOTIFY" "$SUBJECT"
echo "ifx-cycle-nudge sent subject=$SUBJECT form=$FORM_URL"
