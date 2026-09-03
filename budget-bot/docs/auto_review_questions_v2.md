# Auto-review questions v2 (fresh pass)

Prior answers already applied: cafeteria/TGTG → Dining; cleaners → Personal Care; Thorne → Medical; OpenRouter → Software; GoFundMe → Charity; Brother → Shopping; NSF → Irregular; **everything counts toward hardcap**.

Answer only what you care to; leave blank = leave needs_review.

## Still fuzzy merchants — ANSWERED 2026-07-26

| Merchant | Call | Applied |
|----------|------|---------|
| CTLP*J AND J VENDING | Dining Out / food | ✅ rule |
| DVC BOOKSTORE | Shopping (school supplies/snacks) | ✅ rule |
| SEED.COM | Medical & Health (supplements) | ✅ rule |
| 001 DEBTOREDU / DEBTORCC | Irregular (bankruptcy counseling) | ✅ rule |
| CORPORATE FILINGS LLC | Irregular | ✅ rule (prior) |
| MONUMENT 76 | Gas if ≥$15; Misc c-store if smaller | ✅ amount split |
| SQ *DIVINE DOVE | Charity / Donations | ✅ rule |
| VENMO under $20 | Misc auto | ✅ policy |
| VENMO ≥$20 | needs_review | ✅ |

## Policy choices — ANSWERED

**1. Venmo / person-to-person** → Misc auto if under $20; review above.

**2. Opaque POS / MasterMoney REF** → use embedded **4-digit MCC** after `REF#:` (e.g. `5542` = fuel). Merchant parse fixed for REF#+MCC and POS # lines. Residual opaque queue only when no MCC + no keyword.

**3. Annual / lumpy bills** → **(B) monthly reserve** coded: `bills[]` with `amount_cents` or `annual_cents` (/12); `match` regex clears reserve when posted that month. Seeded CSAA $68.92 + VSP $30.93.

## Optional

Merchants that should **always** page you even if rules match:
…
