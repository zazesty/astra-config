# Txn classification (Budget Bot)

## What it is

Rules + MCC + merchant priors — **not** a trained ML classifier. 1H 2026 xlsx and statement imports feed history/priors/baselines.

## MCC (the “4-digit id”)

On many CU MasterMoney lines:

```text
REF#: 6174DJBLS214 5734 - GROK XAI 1450 PAGE MILL …
                 ^^^^
                 ISO MCC
```

- Extract: `import_xlsx.extract_mcc(name)` — regex `REF#:\s*\S+\s+(\d{4})\s*-`
- Map: `auto_review.MCC_MAP` (e.g. **5734 → Software & Tools** 0.9)
- Only works when REF#/MCC is **present in `name`**. Opaque `MasterMoney Card` alone → no MCC.

## Auto-review order (simplified)

1. Repair false transfer tags on debit-card purchases
2. Refresh opaque merchant via `guess_merchant`
3. Keyword RULES → category/confidence
4. MCC hit can win over weak keywords
5. Apply: high-conf auto_accept; Transfer only if **not** a debit-card purchase

## Data sources on box

| Path | Role |
|------|------|
| `~/.local/state/budget-bot/imports/Consolidated_Transactions_1H_2026_No_Transfers.xlsx` | 1H history |
| `.../imports/july_norcal_2026.pdf` | July statement |
| Live Plaid NorCal + PayPal | **Live fill-in only.** NorCal PDF/xlsx is SSOT on (date, amount); Plaid twins are excluded. PayPal + days after the last statement stay Plaid. St. VIN = thrift/clothes (Shopping), not charity. |

## July PDF pitfall

Statement layout:

```text
07/26/2026  Recurring … MasterMoney Card    -5.00
            07/25/2026 REF#: … 5734 - GROK XAI …
```

Parser must **merge** auth-date + `REF#` continuation lines into the parent txn. If it treats the dated REF line as a new txn start, Grok/MCC never attach.

## ATM ACTIVITY AT A GLANCE pitfall (fixed 2026-09-02)

NorCal statements end with a 3-column recap (`date amount date amount date amount`). `_LINE_RE` used to treat those as register rows; unsigned recap amounts parsed as **inflows** and understated spend. Skip the whole section until the next SAVINGS/CHECKING/MONEY MARKET header. August 2026 checking register is 11 deposits + 59 withdrawals = 70 rows; Aug 1 is starting-balance only.

## Plaid description pitfall

Prefer `original_description` when building Hermes `name` for MasterMoney. Otherwise Aug/live rows stay unclassifiable until a statement PDF backfills them.
