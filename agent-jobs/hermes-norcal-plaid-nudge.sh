#!/usr/bin/env bash
# Biweekly email: are 1st Nor Cal MasterMoney labels still opaque?
# Timer fires daily 11:00 PT; script gate ≥14 days since norcal-nudge-last.json.
set -euo pipefail

NOTIFY="${HERMES_NOTIFY_CMD:-/root/astra-config/scripts/notify-email.sh}"
STATE_DIR="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
STAMP="${STATE_DIR}/norcal-nudge-last.json"
INTERVAL_DAYS="${NORCAL_NUDGE_INTERVAL_DAYS:-14}"

mkdir -p "$STATE_DIR"

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
