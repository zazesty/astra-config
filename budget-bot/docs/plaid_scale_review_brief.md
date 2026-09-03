# Plaid scale review brief — Hermes amount-unit / NorCal

**Status:** reviewed 2026-08-04 (Gemini `ask_panel` + OpenAI via OR; Claude credits unusable) → see **`docs/plaid_scale_review_results.md`**  
**Author:** Grok Build (2026-08-03)  
**Priority:** money-correctness before promote  
**Do not:** promote NorCal, force-sync quarantined Item, or write live tokens into git/docs

### Results report (required — SSOT for Grok rework)

Write **exactly** this file when you finish (or when budget runs low — partial OK):

**`/root/hermes-finance/docs/plaid_scale_review_results.md`**

Grok Build will read only that path to rework/fix. Do not bury findings only in chat.

Suggested sections:

1. **Verdict** — one paragraph (safe / needs fix / do-not-promote until …)
2. **Findings** — severity · file:line · issue · fix status (fixed / deferred / n/a)
3. **Changes made** — files touched + what
4. **Tests** — commands run + pass/fail (`pytest …`)
5. **Open questions / escalate** — only if Sonnet cannot close without Opus or human
6. **Promote readiness** — explicit: **do not promote NorCal** unless contracts proven *and* real txns rechecked (still default: hold)

If credits run out mid-pass: still write the report with what you have + “partial”.

---

## Goal

Second pair of eyes on the **100× amount-scale path** for Plaid Items (NorCal CU). Confirm convert/detect/quarantine/promote is correct, testable, and hard to footgun. Ship small fixes + tests if you find real bugs; **do not** promote NorCal in this pass.

## Why this exists

- Hermes stores spend as **integer cents**, sign convention: **positive = outflow / hardcap spend**.
- Normal Plaid depository amounts are **dollars** → convert with `×100`.
- NorCal linked 2026-08-03: **balances look 100× high** (share ~500 → real ~$5; MM ~36 → ~$0.36). User read: “cents shown as dollars.”
- Txns were still `PRODUCT_NOT_READY` at link time (`last_successful_update` null). **Do not promote until scale recheck with real txns.**
- Item is **quarantined** so poll/webhook auto-sync **skips** it.

## Live state (facts, not secrets)

| Fact | Value |
|------|--------|
| Repo | `/root/hermes-finance` |
| State dir | `~/.local/state/hermes-finance/` (mode 700; **never git**) |
| Item | 1st NorCal — quarantine on; `amount_unit` path ready if needed |
| Commands | `cd /root/hermes-finance && python3 -m hermes_finance <cmd>` |
| Ops rails | `/root/OPERATOR.md` § bank data never GitHub; Hermes local-only |

## Files to review (tight set)

| File | Why |
|------|-----|
| `hermes_finance/plaid_sync.py` | Core: `plaid_txn_to_hermes` amount_unit, `assess_scale`, `preview_item`, quarantine skip in `sync_item` / `sync_all_items`, `set_item_flags` |
| `hermes_finance/run.py` | CLI: `plaid-preview`, `plaid-promote`, `plaid-quarantine`, `plaid-sync --force` |
| `hermes_finance/plaid_webhook.py` | Must **not** force-sync quarantined Items |
| `hermes_finance/models.py` | `amount_cents` contract |
| `agent-jobs/hermes-finance-poll.sh` + `hermes-norcal-quarantine-recheck.sh` (astra-config) | Ops path respects quarantine |
| `tests/` | **Gap:** no dedicated scale / amount_unit tests today |

## Correctness contracts (check these)

1. **`amount_unit=dollars` (default):** Plaid `amount` 12.34 → store `1234` cents.
2. **`amount_unit=cents`:** Plaid `amount` 1234 → store `1234` cents (no extra ×100).
3. **Quarantine:** `sync_item` / `sync_all_items` / webhook process **skip** unless explicit force / promote path.
4. **`plaid-preview`:** no cursor write, no txn store write; writes report under `state/quarantine/preview-*.json`; `promote_ok` only when `assess_scale.verdict == "ok"`.
5. **`plaid-promote --amount-unit cents`:** clears quarantine, persists unit, force-sync once. Safe only after preview says scale is understood.
6. **`assess_scale`:** median vs import baseline; balance anchors for NorCal when txns empty; verdicts `ok | likely_100x_high | likely_100x_low | suspect | unknown`.
7. **No double-convert** on re-sync after promote; modified txns re-upsert with same unit.
8. **Balances:** Accounts API `current` may also be wrong-scale; note whether promote path only fixes **txns** (likely) and whether balance display needs a separate unit flag later.

## Suspected soft spots (review, don’t assume)

- Preview converts with Item’s current `amount_unit` (default dollars) then flags 100× — good for detect; confirm promote-with-cents re-preview logic is documented/tested.
- Baseline median needs ≥5 import txns with matching institution hints — NorCal import naming vs Plaid institution string mismatch → false `unknown`.
- Hardcoded July 2026 share/MM anchors in `assess_scale` — brittle after real balances move; still OK as early signal?
- `plaid-promote` calls `set_item_flags` twice when unit set (harmless? race?).
- Webhook + poll: any path that syncs with `force` or `include_quarantine` by accident?
- Pending vs posted amounts; removed txns; multi-account Item.

## Desired deliverables (in priority order)

1. **`docs/plaid_scale_review_results.md`** — required report (see top). Partial if budget tight.
2. **Findings + fixes** — severity + file:line; fix critical/high if clear (also summarized in the report).
3. **Unit tests** for `plaid_txn_to_hermes` dollars vs cents + a couple `assess_scale` fixtures (synthetic batch + fake balances). Put under `tests/test_plaid_scale.py` (or similar). No live Plaid calls.
4. **Doc one-liner** in `docs/session_next.md` or `DECISIONS.md` if promote procedure changes.
5. **Do not:** clear quarantine on NorCal; do not `plaid-sync --force` NorCal; do not commit secrets/tokens/txn dumps.

## How to run tests

```bash
cd /root/hermes-finance
python3 -m pytest tests/ -q
# after adding scale tests:
python3 -m pytest tests/test_plaid_scale.py -q
```

Preview (read-only-ish; may hit Plaid API — OK; must not write txns):

```bash
python3 -m hermes_finance plaid-status
python3 -m hermes_finance plaid-preview --item-id <from status>
```

## Out of scope

- PayPal (already linked, not the scale bug)
- Notify/pace/hardcap rule redesign
- Enabling live Plaid for other FIs
- Rotating any MCP/funnel secrets

## When done

1. **Write** `/root/hermes-finance/docs/plaid_scale_review_results.md` (required).
2. Update this file’s top **Status** → `reviewed` + date + short outcome.
3. One-line pointer in `docs/session_next.md` → results path.
4. Tell Zavdi: promote only after PRODUCT_READY + preview verdict with real txns.
5. Stop. Grok will rework from the results file.

---

*Handoff pointer also in `docs/session_next.md` and standing todo `hermes-plaid-amount-unit-claude-review`.*
