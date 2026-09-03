# Plaid amount-unit / NorCal scale — review results

**Status:** rework-shipped 2026-08-04  
**Panel expansion:** no (not needed)

## Method / cost

| Pass | Seats | Cost (approx) | Quality |
|------|--------|---------------|---------|
| v1 | Gemini (panel) + GPT-4o (~$0.07) + GPT-5 truncated | ~$0.07 | **Low–med.** Gemini specific but 2 false claims; 4o generic. |
| **v2** | Gemini high (`ask_panel`, direct failover) + **`openai/gpt-5.5` high** (~$0.32 BYOK) | ~$0.32+ | **Useful.** GPT-5.5 full writeup with real code cites. |

Artifacts: `/tmp/plaid_review_openai_v2.md`, `/tmp/plaid_review_openai_v2.json`, Gemini seat in session.

**Gemini note:** astra default is `gemini-pro-latest` (OR `~google/gemini-pro-latest`) + `reasoning_effort=high`, not a separate “3.1 pro extended” slug. If you want a hard pin to 3.1-pro later, that’s an astra env/model_slug change — not done here.

---

## How useful was this review?

| Layer | Usefulness |
|-------|------------|
| **Core convert + live quarantine** | Confirmed OK (high confidence). Dollars/cents math, webhook/poll skip — solid. |
| **v1 alone** | Enough to hold NorCal and list medium footguns; **not** enough to trust for deep rework (false claims + weak GPT). |
| **v2 GPT-5.5** | **Worth the ~$0.30.** Found real money-path issues beyond scale: **cursor-before-upsert**, **removed txns ignored**, baseline pollution, invalid unit silent-fallback. |
| **Promote “critical”** | Overstated as a *bug* for this box: promote is intentionally a human CLI. Still valid as **hardening** (require preview / PRODUCT_READY before clear). |

**Net:** Useful for “what to fix before trusting CU live,” not a free pass to promote. Convert path was already mostly right; **sync durability + footguns** are the real rework.

---

## Verdict

**Hold NorCal promote** until PRODUCT_READY txns + re-preview under the intended `amount_unit`.  
Live auto paths (webhook + 15m poll) **do** respect quarantine.

---

## What I would actually rework (ranked)

### Ship soon (clear ROI)

1. **`tests/test_plaid_scale.py`**  
   - dollars 12.34 → 1234; cents 1234 → 1234  
   - quarantine skip without force  
   - preview: no cursor write, no `upsert_txns`  
   - empty cursor omitted from Plaid body  

2. **`plaid-sync --force` footgun** (`run.py`)  
   - Today: no `--item-id` + `--force` → `include_quarantine=True` for **all**.  
   - Change: only include quarantine when `--item-id` is set (or require explicit `--include-quarantine`).  

3. **`amount is None` guard** (`plaid_txn_to_hermes`)  
   - Skip or zero + log; don’t TypeError the whole Item sync.  

4. **Cursor after successful store write** (`_sync_one` / `sync_item` / `sync_all_items`)  
   - Today: cursor persisted inside `_sync_one` **before** `upsert_txns`.  
   - If upsert fails → **lost txns** (cursor advanced).  
   - Fix: return `next_cursor`, upsert (and removals), **then** write cursor.  

5. **Apply Plaid `removed`**  
   - Today: only counted. Deleted/replaced Plaid rows stay in Hermes → possible double-count spend.  
   - Map `plaid-{transaction_id}` → delete/tombstone before cursor advance.  

### Nice hardening (after above)

6. **Promote gate (optional CLI safety)**  
   - Require recent preview with `verdict==ok` + `txn_n>0` under target `amount_unit`, or `--i-know` override.  
   - Not a silent bug today — operator is the gate — but cheap insurance.  

7. **`assess_scale` quality**  
   - Balance 100× hits should **block `promote_ok`** even if txn ratio is `ok` (or at least for norcalish + ≥2 anchors).  
   - Baseline: prefer import/non-plaid, drop pending/transfer/excluded; avoid self-pollution from bad Plaid rows.  
   - Soften checking `2000–50000` false positives.  

8. **Invalid `amount_unit` in items.json**  
   - Anything other than dollars|cents → hard fail, not silent dollars.  

9. **Decimal money** (low priority)  
   - `float`/`round` edge cases; polish later.  

### Do **not** rework / false alarms

- Empty preview cursor → INVALID_CURSOR — **false** (`if cursor:` omits body key).  
- Promote without `--amount-unit` wipes unit — **false** (`set_item_flags` only writes when not None).  
- Webhook auto-ingest of quarantine — **false** (no force).  
- Expand `ask_panel` with GPT — **no** (oracle already seats GPT; panel stay hand-pick grok|gemini).  

---

## Contract matrix (v2)

| Contract | Result |
|----------|--------|
| dollars → *100 | **PASS** |
| cents → no *100 | **PASS** |
| Webhook/poll skip quarantine | **PASS** |
| Preview no cursor/txn ingest | **PASS** (report JSON only) |
| Promote safe for NorCal | **FAIL** as automated gate; OK only as careful human CLI |
| Cursor durability | **FAIL** (write-before-upsert) |
| Removals applied | **FAIL** |

---

## Promote readiness

**Do not promote NorCal** until:

1. Txns PRODUCT_READY (or `txn_count > 0` on preview).  
2. `plaid-preview` under intended unit (set `amount_unit=cents` on Item **before** preview if testing cents path, or promote only after samples match).  
3. Prefer fixes **4–5** (cursor + removals) before relying on live CU for hardcap.  
4. Never bare `plaid-sync --force` while any Item is scale-suspect.  

---

## Shipped 2026-08-04 (rework 1–5)

| # | Change |
|---|--------|
| 1 | `tests/test_plaid_scale.py` — amount_unit, quarantine skip, cursor-after-upsert, removed, force footgun, empty cursor |
| 2 | `plaid-sync --force` without `--item-id` no longer bulk-includes quarantine; add `--include-quarantine` for intentional danger |
| 3 | `plaid_txn_to_hermes` returns `None` on missing amount (skip); invalid unit raises |
| 4 | Cursor written **after** remove+upsert succeed (`write_cursor`); `_sync_one` default `persist_cursor=False` |
| 5 | Plaid `removed` → `remove_txns` before cursor advance |

`python3 -m unittest discover -s tests` → **37 OK**. Still **hold NorCal promote**.
