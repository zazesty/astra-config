# CSAA corner case: bounce → double premium + CSAA fee

## Story

Autopay bounces, then on the next autopay day CSAA takes **two premiums** plus a **~$20 CSAA-tacked fee** (insurer fee — not bank NSF).

## Expected reserve behavior

| Event | Effect |
|-------|--------|
| Premium posts then return/reversal (~same amount, ≤21d) | **Net out** — month stays **unpaid** |
| Two full premiums (two lines or one 2× line) | Clear **two** months (FIFO oldest first) |
| CSAA fee ~$20 (name still matches CSAA) | **Never** clears a month |
| 2× premium + fee in one line | Clears two months; fee leftover unused |
| Premium + fee combined (~monthly+$20) | Clears **one** month only |

## Implementation notes (`rules.py`)

- `build_bill_payment_credits`: name-match spends become a **cent pool**; matching refunds kill a nearby equal spend (bounce).
- Credits require amount **≥ about one full premium** (`monthly − tol`) so small CSAA fees drop out.
- `bill_occurrence_cleared` allocates from the pool inside the due’s clear window; fuzzy path **skips** name-matching blobs (so bounced charges cannot fuzzy-clear).
- Tests: `tests/test_rules.py` → `test_csaa_bounce_then_double_pay_and_fee`.

## Config

- `bill_payment_grace_days: 40` (one-month-late clear)
- `bill_arrears_lookback_months: 6`
