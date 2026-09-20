# Budget Bot — pinned decisions

> **Product name: Budget Bot.** Package/path may still say `hermes-finance` (legacy slug).

## Locked (do not re-litigate without user)

| Decision | Value | When |
|----------|--------|------|
| Hardcap | **$1,050 / calendar month** (`hardcap_cents: 105000`) | 2026-08-29 (was $1,000 from 2026-07-26; $1,111 seed 2026-07-21) |
| Rolling 30d hardcap | **Pinned later** (candidate: 15d past + 15d future) | 2026-07-26 |
| Pace vs bills | **v2 shipped 2026-08-07:** pace uses **committed** = spend + remaining unposted bill reserves (pre-charged); hardcap **breach** uses spend after **annual 1/12 amortization** (known yearly bills only); bills still clear safe-to-spend when posted | 2026-09-05 (amortize) |
| Pace v2.1 lead window | **Superseded 2026-08-18.** Calendar STS/reserves = **rest of current month** (no 7-day lead; no next-month leak). Rolling 30d still uses the 15d-ahead window. Past-due unpaid always reserve; arrears stack 6 months. | 2026-08-18 |
| Import↔Plaid dedupe | **Only on PDF/XLSX import** (not every Plaid upsert). Prefer Plaid when date+amount match import insts | 2026-08-07 |


| Remaining bills | **Reserve until matching post** in period; `day_of_month` = due/overdue coaching only (does not drop unpaid past-due) | 2026-08-03 |
| Recurring auto-detect | Must appear in **current + previous calendar month** or drop (cancel → immediate drop). **Shipped** `hermes_finance recurring` → `recurring.json` | 2026-07-26/27 |
| Push notify | Firm pace + hardcap breach + rare anomalies **plus** one EOM leftover congrats (pri 0). Soft near-pace **culled**. Not mail; not ntfy; new-recurring = on-box only | 2026-08-24 |
| EOM leftover | Calendar STS of **prior** month **minus pending spend**; Pushover `Budget Bot: you saved $X` / `{Month} leftover is $X saved, well done!`; 1st 09:00 PT; skip if leftover ≤ 0; no auto-transfer | 2026-08-24 |
| Daily digests | **OFF** | 2026-08-02 |
| Live notify | **ON** (`notify_enabled: true`) — PayPal coverage until NorCal | 2026-08-02 |
| Anomaly coaching | **ON** rare ($40+ / 3× / once per merchant·month) | 2026-08-02 |
| Pace-hot / soft near-pace | **Culled 2026-08-24.** Firm pace only. | 2026-08-24 |
| Interrupt sleep | Firm over-budget pri 1; **all hardcap breaches pri 2** (incl. further txns while over); else pri 0 | 2026-08-24 |
| Transfers | PayPal↔CU + internal CU savings/MM/checking **excluded** from hardcap | 2026-08-02 |
| Bills | **Annual cadence:** NSSI personal property **$102** (Feb 12), CSAA Renters **$115.73** (Feb 12 endorsement). Leftover 1/12 every month; cash-vs-bills uses the cash pull in the 5d window; charge month spend counts 1/12. **Monthly:** CSAA auto $68.92, Spotify, SuperGrok $30, US Mobile $27, T-Mobile, Apple $0.99, EFF **$25.75 debit card EOM** (`active_from` 2026-08-01), Hetzner ~$15 on ~10th (usage). **Grok + US Mobile `auto_annual`:** a ~10× monthly post (8.5–11.5×; $280/$300) flips the row to annual the same evaluate — named always, opaque MasterMoney in the due window. Spotify/Apple untagged. | 2026-09-05 |
| Cash vs upcoming bills | Canned 4th line only if unpaid **material** dues (≥ half daily allotment) in next **5** days **and** cash < **2×** those dues. Floor ≈ $17.50 on a 30d $1050 month → CSAA / Grok / US Mobile; hide Apple/T-Mobile/Spotify/Hetzner/EFF $25.75 EOM. Overall leftover copy `$N left`. | 2026-09-03 |
| Canned Overall days | Mean of calendar + rolling **pace-days** (ceil fraction). Rolling line always pace, even over cap. Calendar over-cap line still overage (matches breach Push). Do not mix overage into Overall. | 2026-09-08 |
| Product goal | Financial coaching + behavioral optimization | 2026-07-26 |
| Runtime model | Cron + rules (no always-on Hermes Agent process) | 2026-07-26 |
| Grok charges | **SuperGrok $30** = monthly bill; $5/$15 usage = discretionary when charged | 2026-08-18 |
| SaaS tax match | `saas: true` bills allow **~10% over** the pre-tax reserve (plus the usual ±$1) so CA tax-inclusive charges still clear. **Grok / Apple / Spotify** tagged. Not a global ±10% (EFF $25.75 vs US Mobile $27). When an in-band post lands (webhook/poll/evaluate), **`amount_cents` follows the posted amount** so next month reserves $33 not $30. $5/$15 usage is out of band and does not rewrite. | 2026-09-05 |
| Alert channel v0 | Email via `notify-email.sh` (Resend) | 2026-07-21 |
| Alert channel v1 | **Pushover** for firm pace + hardcap breach (`notify-pushover.sh`); Twilio deferred. Soft near-pace culled 2026-08-24. | 2026-08-24 |
| Near-instant review | **Plaid webhooks** primary + **15m poll backup**; sync → auto-review → budget interrupts (no digest) | 2026-08-02 |
| Same-sync dump | **One Pushover** if Plaid lands several new charges in one sync; still one-per-txn when they arrive live | 2026-08-16 |
| Sync-break detection | **ITEM ERROR / LOGIN_REQUIRED = break** — **Pushover immediately** with **24h Funnel re-login URL**; **no email**. Confirm webhook with `/item/get`. **6h suppress** after update-mode Link. **No `/transactions/sync` immediately after update-mode** (NorCal flakes). Poll `/item/get` — stale if last success **>72h**. No scheduled `/transactions/refresh`. | 2026-08-24 |
| NorCal Plaid | Linked 2026-08-03; **quarantined** — balances 100× high; txns not ready; CU still import until promote | 2026-08-03 |
| Cadence | Daily digest (morning PT) + interrupts (hardcap/pace/anomaly) | 2026-07-21 |
| Merchants in coaching | **Yes** — shoulder-taps name merchants | 2026-07-21 |
| Institution order | **PayPal v1** → **1st Nor Cal v1.5/2.0** (on Plaid; deferred by choice) | 2026-07-21 |
| Runtime | No Hermes SaaS sub; box jobs + Grok Build as co-admin builder | 2026-07-21 |
| Package home | `/root/hermes-finance` (Python CLI) + astra-config agent-job | 2026-07-21 (pinned tonight) |
| Amount sign | **Positive cents = spend/outflow** toward hardcap. **Refunds net spend** (credit vouchers / merchant returns). True income (sales, payroll, checks) does not. | 2026-08-20 |
| Pending txns | **Excluded** from hardcap until posted | 2026-07-21 (pinned tonight) |
| Transfers | **Excluded** (`transfer: true`) | 2026-07-21 (pinned tonight) |
| Notify default | **Dry-run** until `notify_enabled: true` or `HERMES_LIVE=1` | 2026-07-21 (pinned tonight) |
| Mode default | `fixture` until Plaid live | 2026-07-21 (pinned tonight) |

## Deferred (need user or live data later)

| Item | Notes |
|------|--------|
| Firm-pace Push → Overall | **Skipped 2026-09-20.** Trigger stays calendar. Do not discuss or ship. |
| ~~Bill lead-time window (pace v2.1)~~ | **Moved to Locked 2026-08-12** — was listed deferred after the code already shipped. |
| Plaid Trial keys | User creates account; store in `/etc/hermes-finance.env` |
| PayPal Link OAuth | User browser session; first live Item |
| 1st Nor Cal Link | After PayPal proven |
| Live email on | Flip `notify_enabled` when digests look right |
| Systemd timer | Wire 2–4×/day after dry-runs look good |
| Bills/goals list | Empty for now; safe-to-spend = hardcap − spend |
| Anomaly thresholds | merchant 3× / category 2.5× / min $15 — tune after real PayPal |
| Token encryption | chmod-only v1 on single-tenant VPS |
| Category mapping | Plaid categories → baseline labels |
| ask_panel weekly | Optional; templates first |
| ntfy / Telegram | Post-email |
| hermes status MCP tool | Optional chat surface |
| Repo remote | Not pushed; local only for now (like early experiments) |

## Wanted v2 (do not drop)

| Item | Notes |
|------|--------|
| **Annual cadence + 1/12 amortize** | ✅ shipped 2026-09-05. `cadence: annual` + `annual_cents` + anniversary `month`/`day_of_month`: leftover always 1/12 (no 12-month arrears stack); cash-vs-bills uses cash pull; charge month hardcap spend = 1/12. Monthly `annual_cents` without cadence still just sizes a monthly due. Seeded NSSI $102 + CSAA Renters $115.73. |

## Data safety (locked)

| Decision | Value |
|----------|--------|
| Bank/txn data → GitHub | **NEVER** (txns, xlsx, tokens, live digests, filled env) |
| Runtime home | `~/.local/state/hermes-finance/` mode 700/600 only |
| Code publish | code + synthetic fixtures only |

## Not chosen (explicit non-decisions)

- No per-category hardcaps
- No MX
- No Cleo-style roast persona

### 2026-08-04 Plaid scale rework
- Cursor written after store upsert/remove (not before).
- Plaid `removed` applied via `remove_txns`.
- `plaid-sync --force` without `--item-id` no longer includes quarantined Items (`--include-quarantine` for intentional bulk).
- Unit tests: `tests/test_plaid_scale.py`.
