# Merchant pins + weekly AI digest (2026-08)

## User-attested pins (opaque MasterMoney)

When Zavdi labels opaque card amounts by lab:

| Rule | Action |
|------|--------|
| Exact cents in ledger | Pin that row only |
| Amount missing (e.g. no $26) | **Skip** — never nearest-neighbor ($27≠$26) |
| Pending / not posted | **Do not pin** |
| Twin $15s | Pin only **posted** rows he listed |
| Apply | `merchant_name` (GROK XAI / OPENROUTER, INC / …); enrich opaque `name`; `category=Software & Tools`; clear false transfer/excluded on debit purchases |
| After | Light auto_review + **regen** AI chart `MEDIA:~/.hermes/image_cache/ai_spend_2026_by_provider.png` |

## Weekly AI card-spend email — OFF (2026-09-06)

- Hermes cron `d237b9f46e18` **paused**. Do not resume or recreate (OPERATOR §3c).
- Script still exists for on-demand: `/root/astra-config/agent-jobs/hermes-ai-cost-digest.sh`
- If ever run: **usage** (Hermes tokens × published rates + OpenRouter billed); card lines as credits bought — never treat a $15 GROK XAI top-up as spend.

## Cost awareness (habit change)

1. Measure first (digest + Console if needed).
2. Hermes gateway this box ~**2026-08-04 23:11 PT** — earlier xAI card hits are not this instance.
3. Default **grok-4.5** burns real credits on heavy tool days.
4. Split: phone triage → Hermes; multi-file deep → Grok Build; then reassess default model.

## Related

- Plot recipe: `references/ai-spend-analysis.md`
- Classification repair: `references/txn-classification.md`
- Photon model routing eval: **photon-imessage** `references/model-routing-photon.md`
