# Bill reserves: horizon, fuzzy clear, arrears

## Config keys (state `config.json`)

| Key | Default | Meaning |
|-----|---------|---------|
| `bill_horizon_days_calendar` | 7 | Calendar upcoming: due within N days (plus overdue/today) |
| `bill_arrears_lookback_months` | 6 | Unpaid past dues stack this far back |
| `bill_payment_grace_days` | 40 | Late post still clears that due (~1 month) |
| `bill_fuzzy_match` | true | Amount match when merchant text opaque |
| `bill_fuzzy_amount_tol_cents` | 100 | ±$1 |
| `bill_fuzzy_day_slop` | 1 | due±1 before grace window expands |

## Occurrence model

Each bill with `day_of_month` generates monthly dues. Reserve = sum of **uncleared** dues in range.

- **Past/today** (within lookback): always candidates until paid.
- **Future calendar:** only if `due ≤ as_of + horizon`.
- **Future rolling:** only if due falls in rolling window end.

Clear: exact name regex in window, else fuzzy amount. **FIFO** — claim oldest unpaid first.

Clear window end = `max(due + grace_days, next_due − 1 day)` so a payment one month late maps to the original due.

## Tests

`tests/test_rules.py`: `test_arrears_stack_unpaid_months`, horizon, fuzzy. Single-shot tests set `arrears_lookback_months=0`.
