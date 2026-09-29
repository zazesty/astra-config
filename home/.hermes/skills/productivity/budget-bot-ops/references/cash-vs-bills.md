# Cash-vs-bills: name the dues

Canned Photon extra line (2026-09-20): per-CU checking vs unpaid material dues. Show when cash < 2× those dues. Stay until the named post clears, capped at `bill_payment_grace_days` (40). Horizon is **3 days**, not calendar 7d.

**Not** `snapshot.bills_reserved_cents` (pace v2). Different window, different amount (cash pull, not 1/12).

## Material floor

`round((hardcap_cents / days_in_period) / 2)`. At $1050 / 30d that is $17.50. Spotify, T-Mobile, Apple, Hetzner often drop under it. Annuals use the lump (`bill_cash_pull_cents`), so NSSI/CSAA renters/USM insurance can show in-window.

## When he asks which / when

Reuse the live unpaid set, do not recite `config.json` `bills[]`.

```python
# cd /root/budget-bot && PYTHONPATH=/root/budget-bot python3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from budget_bot.config import load_config
from budget_bot.store import load_txns, load_balances
from budget_bot.balances import checking_cash_by_cu
from budget_bot.rules import (
    CASH_BILLS_HORIZON_DAYS,
    annual_in_band_txn_ids,
    bill_cash_pile,
    bill_cash_pull_cents,
    bill_due_dates_in_range,
    bill_occurrence_cleared,
    build_bill_payment_credits,
    canned_cash_bills_by_cu,
    evaluate_budget_both,
)

cfg = load_config()
txns = load_txns()
as_of = datetime.now(ZoneInfo(cfg.get("timezone") or "America/Los_Angeles")).date()
cal = evaluate_budget_both(txns, cfg, as_of=as_of)["calendar"]
print(canned_cash_bills_by_cu(
    cfg.get("bills"), txns, as_of,
    hardcap_cents=cal.hardcap_cents,
    days_in_period=cal.days_in_period,
    cash_by_cu=checking_cash_by_cu(load_balances()),
    exclude_pending=bool(cfg.get("exclude_pending", True)),
    fuzzy=bool(cfg.get("bill_fuzzy_match", True)),
    fuzzy_amount_tol_cents=int(cfg.get("bill_fuzzy_amount_tol_cents", 100)),
    fuzzy_day_slop=int(cfg.get("bill_fuzzy_day_slop", 2)),
    payment_grace_days=int(cfg.get("bill_payment_grace_days", 40)),
))
# Then list uncleared dues in [as_of-grace, as_of+3] with pull >= floor, same pile.
```

Photon reply: one line per due — name, dollars, date; say if still open past due. Stop.

Never print access tokens or txn rows.
