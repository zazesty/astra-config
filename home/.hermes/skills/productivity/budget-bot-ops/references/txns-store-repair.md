# Repair torn `txns.json`

## Symptom

```
json.decoder.JSONDecodeError: Extra data: line N column 1
```

`budget-status` / `status` / poll paths that `load_txns()` all fail.

## Cause

Valid JSON array written, then a **garbage tail** (torn concurrent write). First value parses; leftover starts after the closing `]`.

## Fix

```bash
python3 - <<'PY'
import json, shutil
from pathlib import Path
from datetime import datetime, timezone
p = Path.home() / ".local/state/hermes-finance/txns.json"
text = p.read_text()
obj, idx = json.JSONDecoder().raw_decode(text)
bak = p.with_suffix(
    f".json.bak-torn-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
)
shutil.copy2(p, bak)
p.write_text(json.dumps(obj, indent=2) + "\n")
print("ok count=", len(obj), "removed=", len(text) - idx, "bak=", bak)
json.loads(p.read_text())  # verify
PY
cd /root/budget-bot && python3 -m budget_bot budget-status
```

Do not hand-edit txn bodies in chat. Keep the bak until confirmed.
