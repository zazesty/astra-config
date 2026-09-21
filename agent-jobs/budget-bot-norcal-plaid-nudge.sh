#!/usr/bin/env bash
# Biweekly email: are 1st Nor Cal MasterMoney labels still opaque?
# Timer fires daily 11:00 PT; script gate ≥14 days since norcal-nudge-last.json.
set -euo pipefail

NOTIFY="${BUDGET_BOT_NOTIFY_CMD:-${HERMES_NOTIFY_CMD:-/root/astra-config/scripts/notify-email.sh}}"
STATE_DIR="${BUDGET_BOT_STATE:-${HERMES_FINANCE_STATE:-$HOME/.local/state/budget-bot}}"
STAMP="${STATE_DIR}/norcal-nudge-last.json"
INTERVAL_DAYS="${NORCAL_NUDGE_INTERVAL_DAYS:-14}"

mkdir -p "$STATE_DIR"

# Detector: skip nag when live Plaid names (or Alliant spend) are already clear.
if python3 - "$STATE_DIR" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]) / "names_health.json"
if not p.is_file():
    raise SystemExit(0)
try:
    data = json.loads(p.read_text())
except Exception:
    raise SystemExit(0)
if data.get("names_live"):
    print("skip: names_live")
    raise SystemExit(3)
raise SystemExit(0)
PY
then
  :
else
  rc=$?
  if [ "$rc" = "3" ]; then
    echo "nudge skipped (names_live)"
    exit 0
  fi
fi

if ! python3 - "$STAMP" "$INTERVAL_DAYS" <<'PY'
import json, sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

stamp_path = Path(sys.argv[1])
interval = int(sys.argv[2])
today = datetime.now(ZoneInfo("America/Los_Angeles")).date()

if stamp_path.is_file():
    try:
        data = json.loads(stamp_path.read_text())
        last_s = data.get("last_sent") or data.get("date")
        last = date.fromisoformat(str(last_s)[:10])
    except Exception:
        last = None
    if last is not None and (today - last).days < interval:
        print(f"skip: last_sent={last} interval={interval}d today={today}")
        sys.exit(2)
sys.exit(0)
PY
then
  echo "nudge skipped (biweekly gate)"
  exit 0
fi

SUBJECT="Budget Bot: are NorCal labels still MasterMoney?"
BODY="$(cat <<'EOF'
Are new 1st Nor Cal debits still generic "MasterMoney Card", or are real merchants showing up?

If still opaque: no action. Budget Bot still counts spend.

— Budget Bot (every 2 weeks)
EOF
)"

printf '%s\n' "$BODY" | "$NOTIFY" "$SUBJECT"

python3 - "$STAMP" "$INTERVAL_DAYS" <<'PY'
import json, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
p = Path(sys.argv[1])
interval = int(sys.argv[2])
today = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
p.write_text(json.dumps({
    "last_sent": today,
    "interval_days": interval,
    "note": "MasterMoney labels only",
}, indent=2) + "\n")
p.chmod(0o600)
print("stamped", today)
PY

echo "nudge sent subject=$SUBJECT"
