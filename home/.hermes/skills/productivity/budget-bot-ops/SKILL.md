---
name: budget-bot-ops
description: "Use when Budget Bot Plaid, bills, alerts, or txn repair."
version: 1.3.2
author: zaz-astra
platforms: [linux]
metadata:
  hermes:
    tags: [budget, finance, plaid, pace, hardcap, pushover, ops]
    category: productivity
    related_skills: [budget-bot, photon-imessage, astra-memory-bridge]
---

# Budget Bot ops (class)

Companion to product skill **budget-bot** (status CLI; adopt if curator should own it). This skill covers **ops**: bills semantics, alert dedup, Plaid quarantine/Link, txn store repair, sync-break notify.

**Photon:** status/plaid-status, or name the dues behind the canned cash-vs-bills line (read-only). Any code, copy, or policy change → skill **standing-todos**. Do not patch from Photon.

Code: `/root/budget-bot`. State: `~/.local/state/budget-bot/`. Never print access tokens or full txn dumps.

## Status first

```bash
cd /root/budget-bot && python3 -m budget_bot budget-status
```

Photon reply: paste the CLI line. Numbers from CLI only. Same rolling sentence as Pushover.

## Bills (`+$N bills`) — policy 2026-08-10 (+ arrears)

| Period | What reserves count |
|--------|-------------------|
| **Calendar** | Unpaid **occurrences**: overdue/today + due later this month; plus **stacked arrears** |
| **Rolling** (Photon and Pushover) | Unpaid dues in the 21+7 window + **same arrears stack** |

**Arrears stacking (both periods):** each missed monthly due within `bill_arrears_lookback_months` (default **6**) adds +monthly until cleared. Payments clear **oldest first** (FIFO).

**Clear window per due:** exact `match` **or fuzzy** ($ ± default $1). Window = `(due − slop) … max(due + bill_payment_grace_days, day before next due)`. Default grace **40** → **~1 month late** still clears the original due.

- Pace v2: committed = spend + bills_reserved. The day count uses committed, on the rolling window.
- Config: state `config.json` + `budget_bot/config.py`. Tighter status when true misses stack is intended.
- Detail: `references/bills-arrears.md`.

## Cash-vs-bills follow-up ("what bills are those?")

The canned extra line (`Alliant: $144 cash > $83 bills`) is **not** calendar/rolling `bills_reserved_cents`. It is unpaid **material** dues for that CU in the 3d horizon, staying until a named post clears (grace 40d).

When he asks which bills / when: walk the same unpaid set `canned_cash_bills_by_cu` uses. Photon: name, dollars, due date; mark still-open past dues. No `txns.json`. Recipe: `references/cash-vs-bills.md`.

## Pace framing (`days_off_pace`)

- `(committed/hardcap)*days_in − days_elapsed`. **Positive** = days ahead of spend schedule (over pace).
- Photon and both pushes use the rolling count (21 back, today, 7 ahead). `{N} days ahead of pace.` Under: `{N} days under pace.` Loud at 5 or more. No percent of cap. The calendar dollar-cap sentence is retired (2026-09-27).
- The plaid webhook is a long-lived process. After a copy or rules change, restart `budget-bot-plaid-webhook` or the next push still uses the old in-memory sentence.
- Soft hardcap-warn Pushover: subject `Budget Bot: near pace`; body `Spend pace is near allotted. Committed $X versus $Y allotted.` No merchant names on pace. No hardcap/safe-to-spend/pro-rate jargon.
- Leftover is `you saved $X`. Anomaly is `unusual MERCHANT $X`. Sync-break is `NorCal needs a re-login`.

## Alert silence

- `pace_firm|YYYY-MM` **deduped** in `notified_keys.json` → `skipped_dedup` is normal.
- Digests off when `digest_enabled: false`.
- Failed poll/sync → no new txn-driven alerts. Check `notify.log`, `last_run.json`, `budget-bot-poll.service`.

## Sync-break alerts (2026-08-10)

- `sync_health.py` on each process cycle: **Resend email immediately** on Item failure or LOGIN_REQUIRED quarantine; **Pushover after `sync_break_pushover_after_days` (3)** if still broken.
- `sync_all_items` records `failed_items` per Item (one bad bank must not kill PayPal).

## Plaid

| Pattern | Do |
|---------|-----|
| One Item `ITEM_LOGIN_REQUIRED` | Quarantine **that** Item |
| Re-auth | `plaid-link --funnel` → public URL → bank; also watch `LOGIN_REPAIRED` webhooks |
| Probe | `load_access_token(item_dict)` from `list_items`, not bare id |
| Safari “string did not match the expected pattern” | Non-JSON body to `r.json()` — **not** Plaid API rework — see `references/plaid-ops.md` |

```bash
python3 -m budget_bot plaid-status
python3 -m budget_bot plaid-quarantine --item-id <ID> --reason 'ITEM_LOGIN_REQUIRED …'
python3 -m budget_bot plaid-link --funnel --timeout 1800 --port 8787
python3 -m budget_bot plaid-webhook-process
```

Recipe: `references/plaid-ops.md`.

## Torn `txns.json`

`JSONDecodeError: Extra data` → valid array + garbage tail.

1. Backup  2. `raw_decode` first value  3. Rewrite clean  4. `budget-status`

See `references/txns-store-repair.md`.

## Pitfalls

1. Inventing spend without CLI
2. Calendar *upcoming* still 7d-horizon (Spotify day+9 may be out); past dues still stack as arrears
2b. Answering "what bills are those?" from pace `bills_reserved_cents` or the static `bills[]` list — the canned `$N bills` is only unpaid material dues in that CU's 3d/40d window
3. Expecting second firm-pace push same month (dedup)
4. One bad Plaid Item killing poll — quarantine + per-item catch
5. Trusting Link UI “failed” over webhook/`item_get`+sync after LOGIN_REPAIRED
6. Treating Safari exchange SyntaxError as Plaid API rework (usually non-JSON response)
7. Tokens/PHI in chat or git
8. Re-linking NorCal or parking a Plaid scrub. Soft-close (2026-09-29) removed the Item. Do not re-link. A statement import sets the close to the end of that statement's month and drops NorCal rows after it. A later statement extends the close. History through the close stays.

## Verification

- [ ] status CLI clean; days-ahead when off pace
- [ ] Calendar near-horizon bills; rolling can include more
- [ ] Bad Items quarantined; Link only when needed
- [ ] Alert silence = dedup and/or sync failure
- [ ] Sync-break email path known for LOGIN_REQUIRED
