# Plaid ops (Budget Bot)

## Typical Items

| Institution | Notes |
|-------------|--------|
| PayPal | Usually more stable (OAuth/API-native + mature Plaid path); still can break |
| 1st Northern California CU | `ITEM_LOGIN_REQUIRED` every few months or after password/MFA — quarantine + re-Link |

State: `~/.local/state/budget-bot/tokens/` (`items.json`, per-item token JSON, `.cursor`).

## Probe which Item fails

`load_access_token` takes the **item dict** from `list_items()`, not a bare id string.

```python
from budget_bot.plaid_sync import list_items, load_access_token
from budget_bot.plaid_client import transactions_sync, item_get
from pathlib import Path

for i in list_items():
    iid, inst = i["item_id"], i.get("institution")
    tok = load_access_token(i)
    try:
        err = (item_get(tok).get("item") or {}).get("error")
        print(inst, "item_get.error", err)
    except Exception as e:
        print(inst, "item_get FAIL", e)
    cur_p = Path(f"/root/.local/state/budget-bot/tokens/{iid}.cursor")
    cur = cur_p.read_text().strip() if cur_p.exists() else ""
    try:
        r = transactions_sync(tok, cur)
        print("OK", inst, "added", len(r.get("added") or []))
    except Exception as e:
        print("FAIL", inst, str(e)[:300])
```

## Quarantine so poll survives

```bash
cd /root/budget-bot
python3 -m budget_bot plaid-quarantine \
  --item-id <ITEM_ID> \
  --reason 'ITEM_LOGIN_REQUIRED YYYY-MM-DD'
python3 -m budget_bot plaid-webhook-process
```

Expect `skipped_quarantine` for the bad Item; healthy Item should still sync. Sync-break emails on LOGIN_REQUIRED quarantine via `sync_health.py`.

## Re-Link

```bash
cd /root/budget-bot
python3 -m budget_bot plaid-link --funnel --timeout 1800 --port 8787
```

- Send **public_url** only. User picks the bank (e.g. 1st NorCal).
- Leave process running until success; then clear quarantine + `plaid-webhook-process`.
- Check `webhook.log` for **`LOGIN_REPAIRED`** — can heal the **existing** Item even if browser UI shows exchange failure. **Probe sync before forcing another Link.**

### Safari “Exchange failed: SyntaxError: The string did not match the expected pattern”

**Not** a Plaid API format rewrite. Safari throws that when `response.json()` gets **non-JSON** (HTML/empty/proxy). 

Mitigations in `plaid_link_server.py` (2026-08-10): client `readJson` + pathname mount; server always JSON on `/exchange`.

`timeout waiting for Link` = CLI waiter gave up on UI success — orthogonal to whether LOGIN_REPAIRED already fixed the Item.

## Notify vs sync

- No successful sync + no new txns → no new anomaly/per-txn alerts.
- `pace_firm|YYYY-MM` in `notified_keys.json` → further firm pace = `skipped_dedup`.
- Sync still broken N days → Pushover after `sync_break_pushover_after_days` (default 3); email on first break.
