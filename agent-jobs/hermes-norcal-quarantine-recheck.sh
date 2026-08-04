#!/usr/bin/env bash
# Weekly: re-preview quarantined NorCal Item (no live txn promote).
# Email only on interesting change, or always a short status if --always.
set -euo pipefail

export HOME="${HOME:-/root}"
HERMES_ROOT="${HERMES_ROOT:-/root/hermes-finance}"
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="${HERMES_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
NOTIFY="${HERMES_NOTIFY_CMD:-/root/astra-config/scripts/notify-email.sh}"
STATE_DIR="$HERMES_FINANCE_STATE/quarantine"
mkdir -p "$STATE_DIR"
LAST="$STATE_DIR/norcal-recheck-last.json"
ALWAYS=0
[[ "${1:-}" == "--always" ]] && ALWAYS=1

REPORT="$(cd "$HERMES_ROOT" && python3 - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
from hermes_finance.plaid_client import item_get
from hermes_finance.plaid_sync import list_items, load_access_token, preview_item, set_item_flags

items = list_items()
nor = next(
    (
        i
        for i in items
        if i.get("quarantine")
        and (
            "nor" in (i.get("institution") or "").lower()
            or "calif" in (i.get("institution") or "").lower()
            or "credit-union" in (i.get("institution") or "").lower()
        )
    ),
    None,
)
# fallback: any quarantined item
if nor is None:
    nor = next((i for i in items if i.get("quarantine")), None)

out = {
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "found": bool(nor),
}
if not nor:
    out["note"] = "no quarantined Item"
    print(json.dumps(out))
    raise SystemExit(0)

iid = nor["item_id"]
out["item_id"] = iid
out["institution"] = nor.get("institution")
out["quarantine"] = True
out["amount_unit"] = nor.get("amount_unit") or "dollars"

# item/get txn health
try:
    ig = item_get(load_access_token(nor))
    st = (ig.get("status") or {}).get("transactions") or {}
    out["txn_last_success"] = st.get("last_successful_update")
    out["txn_last_failed"] = st.get("last_failed_update")
    out["item_error"] = (ig.get("item") or {}).get("error")
except Exception as e:
    out["item_get_error"] = str(e)[:300]

prev = preview_item(item_id=iid)
rep = (prev.get("items") or [{}])[0]
scale = rep.get("scale") or {}
out["txn_count"] = rep.get("txn_count")
out["verdict"] = scale.get("verdict")
out["ratio"] = scale.get("ratio")
out["balance_100x_hits"] = scale.get("balance_100x_hits")
out["promote_ok"] = rep.get("promote_ok")
out["notes"] = (scale.get("notes") or [])[:6]
out["quarantine_path"] = rep.get("quarantine_path")

# interesting?
txn_ready = bool(out.get("txn_last_success")) or (out.get("txn_count") or 0) > 0
interesting = bool(
    out.get("promote_ok")
    or txn_ready
    or out.get("verdict") in ("ok", "likely_100x_high", "likely_100x_low", "suspect")
    and (out.get("txn_count") or 0) > 0
)
out["interesting"] = interesting or txn_ready
out["txn_ready"] = txn_ready

# If promote_ok with dollars, still leave quarantine — human/agent promotes.
# If verdict likely_100x_high AND txns present, suggest amount_unit=cents in notes.
if out.get("verdict") == "likely_100x_high" and (out.get("txn_count") or 0) > 0:
    out["suggested_next"] = (
        f"plaid-promote --item-id {iid} --amount-unit cents  # after you confirm samples"
    )
elif out.get("promote_ok") and (out.get("txn_count") or 0) > 0:
    out["suggested_next"] = f"plaid-promote --item-id {iid}  # scale looks ok"
else:
    out["suggested_next"] = "keep quarantine; still waiting on clean scale + txns"

print(json.dumps(out))
PY
)"

echo "$REPORT" >"$STATE_DIR/norcal-recheck-latest.json"
chmod 600 "$STATE_DIR/norcal-recheck-latest.json" 2>/dev/null || true

# decide email
SHOULD=0
if [[ "$ALWAYS" == "1" ]]; then
  SHOULD=1
else
  SHOULD="$(REPORT_JSON="$REPORT" LAST_PATH="$LAST" python3 - <<'PY'
import json, os
from pathlib import Path
cur = json.loads(os.environ["REPORT_JSON"])
last_p = Path(os.environ["LAST_PATH"])
last = {}
if last_p.is_file():
    try:
        last = json.loads(last_p.read_text())
    except Exception:
        last = {}
# email if: first run, txn became ready, verdict changed, promote_ok flipped, txn_count jumped 0→n
keys = ("verdict", "promote_ok", "txn_ready", "txn_count", "balance_100x_hits")
changed = any(cur.get(k) != last.get(k) for k in keys)
if not last or cur.get("interesting") and changed or cur.get("txn_ready") and not last.get("txn_ready"):
    print(1)
else:
    print(0)
PY
)"
fi

echo "$REPORT" >"$LAST"
chmod 600 "$LAST" 2>/dev/null || true

if [[ "$SHOULD" != "1" ]]; then
  echo "norcal-recheck: no material change (silent). verdict=$(echo "$REPORT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("verdict"))')"
  exit 0
fi

SUBJECT="$(echo "$REPORT" | python3 -c '
import sys,json
o=json.load(sys.stdin)
v=o.get("verdict") or "?"
n=o.get("txn_count")
ready="ready" if o.get("txn_ready") else "txns-pending"
print(f"Budget Bot · NorCal quarantine recheck · {v} · {ready} · n={n}")
')"

BODY="$(echo "$REPORT" | python3 -c '
import sys,json
o=json.load(sys.stdin)
notes="\n".join(f"  - {n}" for n in (o.get("notes") or []))
print(f"""NorCal Plaid quarantine recheck (no live promote)

Institution: {o.get("institution")}
Item:        {o.get("item_id","")[:20]}…
Quarantine:  still on (auto-sync skipped)
amount_unit: {o.get("amount_unit")}

Txn product: last_success={o.get("txn_last_success")}
             last_failed={o.get("txn_last_failed")}
Preview txns: {o.get("txn_count")}
Scale verdict: {o.get("verdict")}  promote_ok={o.get("promote_ok")}
Balance 100× hits: {o.get("balance_100x_hits")}
Ratio (txn median vs import): {o.get("ratio")}

Notes:
{notes or "  (none)"}

Suggested next:
  {o.get("suggested_next")}

Report: {o.get("quarantine_path")}
— hermes-norcal-quarantine-recheck.timer
""")
')"

printf '%s\n' "$BODY" | "$NOTIFY" "$SUBJECT"
echo "norcal-recheck: emailed subject=$SUBJECT"
