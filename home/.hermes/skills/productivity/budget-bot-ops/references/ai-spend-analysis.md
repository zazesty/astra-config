# AI spend by provider (from Budget Bot ledger)

## When

User asks AI/API/model lab spend over time (xAI vs Anthropic, monthly, YTD).

## Steps

1. Prefer cleaned store: if MasterMoney AI lines look `transfer`/`excluded`, run repair + `auto_review` first (see txn-classification.md).
2. Scan `txns.json` (or `load_txns()`): match on **merchant_name + name**:
   - Anthropic / Claude.AI / Claude Sub
   - xAI / Grok
   - OpenRouter
   - OpenAI / ChatGPT, Cursor, etc. as needed
3. Count only `amount_cents > 0` and not transfer/excluded/pending (after repair).
4. Aggregate by month + lab; plot stacked bars; totals table.
5. State coverage limits (e.g. no Aug merchant text yet).

## Paths

- Store: `~/.local/state/budget-bot/txns.json`
- Chart out: `~/.hermes/image_cache/ai_spend_2026_by_provider.png`
- Photon: `MEDIA:/absolute/path.png`

## Do not

- Invent months with no ledger rows
- Claim OpenAI/Google spend without matching txns
- Dump full merchant lists with bank tokens into chat
