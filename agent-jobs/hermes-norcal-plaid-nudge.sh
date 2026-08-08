#!/usr/bin/env bash
# Biweekly nudge: check whether 1st Nor Cal Plaid still works + opaque MasterMoney naming.
# Armed via hermes-norcal-plaid-nudge.timer (checks daily 11:00 PT; sends at most every 14 days).
# Survives agent sessions — pure box systemd + Resend email.
set -euo pipefail

NOTIFY="${HERMES_NOTIFY_CMD:-/root/astra-config/scripts/notify-email.sh}"
HERMES_ROOT="${HERMES_ROOT:-/root/hermes-finance}"
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="${HERMES_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
STATE_DIR="${HERMES_FINANCE_STATE}"
STAMP="${STATE_DIR}/norcal-nudge-last.json"
INTERVAL_DAYS="${NORCAL_NUDGE_INTERVAL_DAYS:-14}"

mkdir -p "$STATE_DIR"

# Gate: only send if ≥ INTERVAL_DAYS since last send (or never sent).
if ! python3 - "$STAMP" "$INTERVAL_DAYS" <<'PY'
import json, sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

stamp_path = Path(sys.argv[1])
interval = int(sys.argv[2])
tz = ZoneInfo("America/Los_Angeles")
today = datetime.now(tz).date()

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

STATUS_JSON="{}"
SAMPLE_JSON="[]"
if [ -d "$HERMES_ROOT" ]; then
  STATUS_JSON="$(cd "$HERMES_ROOT" && python3 -m hermes_finance plaid-status 2>/dev/null || echo '{}')"
  SAMPLE_JSON="$(cd "$HERMES_ROOT" && python3 - <<'PY' 2>/dev/null || echo '[]'
import json
from hermes_finance.store import load_txns
rows = [
    t for t in load_txns()
    if (t.institution or "") == "1st-northern-california-credit-union"
    and "MasterMoney" in (t.name or "")
]
rows = sorted(rows, key=lambda t: t.date or "", reverse=True)[:8]
out = [
    {
        "date": t.date,
        "amount_cents": t.amount_cents,
        "name": (t.name or "")[:80],
        "merchant_name": t.merchant_name,
    }
    for t in rows
]
print(json.dumps(out, indent=2))
PY
)"
fi

SUBJECT="Budget Bot: NorCal Plaid check (biweekly)"
BODY="$(cat <<EOF
Biweekly NorCal / Plaid check (Budget Bot)

Cadence: every ${INTERVAL_DAYS} days (not weekly).

What to glance at (2 min):
1. Opaque naming — are new CU debits still generic "MasterMoney Card", or do real merchants (Hetzner, etc.) show up yet?
2. Link health — does Plaid still sync, or do you need to re-open Link / log in again at 1st Nor Cal?
3. If Link broke: ask the box for a fresh Plaid Link URL (Funnel), reconnect 1st Northern California.
4. If naming is still opaque: no action required; Budget Bot still counts spend, labels just suck until their backend improves.

Recent MasterMoney-ish lines (sample):
${SAMPLE_JSON}

Linked Items (no secrets):
${STATUS_JSON}

— zaz-astra · hermes-norcal-plaid-nudge.timer (biweekly)
EOF
)"

printf '%s\n' "$BODY" | "$NOTIFY" "$SUBJECT"

# Record send time (PT calendar date)
python3 - "$STAMP" <<'PY'
import json, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
p = Path(sys.argv[1])
today = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
p.write_text(json.dumps({"last_sent": today, "interval_days": 14}, indent=2) + "\n")
p.chmod(0o600)
print("stamped", today)
PY

echo "nudge sent subject=$SUBJECT"
