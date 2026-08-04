# Budget Bot — Twilio A2P campaign resubmit kit

**Edit the rejected campaign** (do not delete/recreate). Console → Messaging → Regulatory Compliance → Campaigns → failed campaign → **Edit Campaign** → paste fields below → resubmit.

Rejection fixed here:

| Code | Issue | Fix |
|------|--------|-----|
| **30886** | Campaign description too vague | Use full **Campaign description** below (who / who receives / why) |
| **30909** | Message Flow / CTA incomplete | Use full **Message Flow** below (every opt-in path + disclosures + legal URLs) |

Public evidence (already live, no login):

- Program site: https://zaz-astra.tail5d74e1.ts.net/budget-bot/
- Privacy: https://zaz-astra.tail5d74e1.ts.net/budget-bot/privacy.html
- Terms: https://zaz-astra.tail5d74e1.ts.net/budget-bot/terms.html
- Message flow page: https://zaz-astra.tail5d74e1.ts.net/budget-bot/message-flow.html

---

## 1. Use case

Keep **STARTER** (Sole Proprietor) if that is what the brand allows. Do not switch to Marketing.

---

## 2. Campaign description (30886)

Paste **exactly** (or close; 40–4096 chars). Explains sender, recipients, purpose:

```
Budget Bot (operated by sole proprietor Zavdi) sends personal budget coaching SMS alerts to the single enrolled mobile subscriber who completed double opt-in. Messages are account-notification style only: hardcap near-limit warnings, hardcap breach alerts, and spending pace warnings when the subscriber’s monthly spending approaches or exceeds their configured hardcap. Recipients are only numbers that replied YES after receiving required disclosures; this campaign does not send marketing, promotions, coupons, or third-party advertising. Support: zazesty@gmail.com. Program info: https://zaz-astra.tail5d74e1.ts.net/budget-bot/
```

---

## 3. Message Flow / Call to Action (30909)

Paste **exactly** into the Message Flow field (40–2049 chars). Do **not** paste only a URL.

```
End users opt in by SMS double opt-in only (no website form; no pre-checked box). Path 1: user texts BUDGET or START to the Budget Bot long code on this campaign; Budget Bot replies with a welcome that identifies Budget Bot (operated by Zavdi), describes hardcap and pace budget alerts only (not marketing), states message frequency varies (typically fewer than 10 messages per month), states message and data rates may apply, gives STOP and HELP instructions, links Privacy https://zaz-astra.tail5d74e1.ts.net/budget-bot/privacy.html and Terms https://zaz-astra.tail5d74e1.ts.net/budget-bot/terms.html, and asks the user to reply YES to enroll. Path 2: Budget Bot may send that same welcome invitation once to a mobile number intended for enrollment; the user must still reply YES. After YES, Budget Bot sends a confirmation that enrollment is complete and that only hardcap/pace alerts will be sent, again including frequency, rates, STOP/HELP, and Privacy/Terms links. No program alerts are sent until YES is received. Opt-out: STOP. Help: HELP or zazesty@gmail.com. Privacy policy states mobile numbers are not sold or shared with third parties for their marketing. Full flow with sample SMS text: https://zaz-astra.tail5d74e1.ts.net/budget-bot/message-flow.html
```

---

## 4. Sample messages (min 2)

Use these (brand named; STOP language; align with description). Bracket variables are OK:

**Sample 1**
```
Budget Bot: Hardcap warning — spend is near your $[LIMIT] monthly limit ([PERCENT]% used). Reply STOP to cancel, HELP for help. Msg & data rates may apply.
```

**Sample 2**
```
Budget Bot: Hardcap breach — spend exceeded your $[LIMIT] monthly limit. Reply STOP to cancel, HELP for help. Msg & data rates may apply.
```

**Sample 3 (optional)**
```
Budget Bot: Pace warning — current spend pace may exceed your $[LIMIT] monthly hardcap. Reply STOP to cancel, HELP for help. Msg & data rates may apply.
```

---

## 5. Embedded content flags

| Field | Value | Why |
|-------|--------|-----|
| Messages will include embedded links | **Yes** | Welcome/confirm include Privacy & Terms URLs |
| Messages will include phone numbers | **No** | Samples do not include a phone number |

---

## 6. Keyword / auto-reply fields

If Console shows these (fill when present — keyword opt-in is used):

**Opt-in keywords**
```
BUDGET,START,YES
```

**Opt-in message** (confirmation after YES; keep ≤320 chars)
```
Budget Bot: You’re confirmed for Budget Bot SMS. We’ll text only for hardcap/pace budget alerts. Msg frequency varies (typically <10/mo). Msg & data rates may apply. Reply STOP to cancel, HELP for help. Privacy: https://zaz-astra.tail5d74e1.ts.net/budget-bot/privacy.html
```

**Opt-out keywords** (if not using Twilio Advanced Opt-Out defaults)
```
STOP,STOPALL,UNSUBSCRIBE,CANCEL,END,QUIT
```

**Opt-out message**
```
Budget Bot: You are unsubscribed from Budget Bot SMS. No more messages will be sent. Reply START or BUDGET to re-subscribe later.
```

**Help keywords**
```
HELP,INFO
```

**Help message**
```
Budget Bot: Budget Bot sends hardcap & pace budget alerts only. Msg frequency varies. Msg & data rates may apply. Reply STOP to cancel. Help: zazesty@gmail.com Privacy: https://zaz-astra.tail5d74e1.ts.net/budget-bot/privacy.html
```

If Twilio Advanced Opt-Out is enabled on the Messaging Service, you may leave opt-out/help to defaults — still fill opt-in keywords + opt-in message because keyword opt-in is offered.

---

## 7. Privacy / Terms URL fields (if separate)

- Privacy: `https://zaz-astra.tail5d74e1.ts.net/budget-bot/privacy.html`
- Terms: `https://zaz-astra.tail5d74e1.ts.net/budget-bot/terms.html`
- Website / brand site (if asked): `https://zaz-astra.tail5d74e1.ts.net/budget-bot/`

Privacy already states: no sharing/selling of mobile numbers for third-party marketing; frequency; msg & data rates.

---

## 8. After resubmit

1. Save/resubmit the **same** campaign SID (`CMcae012931590a65524407a48ef94c0b3`) when possible — re-vetting fee is usually once per campaign.
2. Wait for TCR review (sole prop: often hours–days).
3. When **Approved**, put Twilio creds in `/etc/hermes-finance.env`:
   ```
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_FROM=+1...
   TWILIO_TO=+1...
   ```
4. Test: `bash /root/astra-config/scripts/notify-sms.sh "Budget Bot test"`
5. iOS: save FROM as contact → allow through Focus / Emergency Bypass.

---

## 9. Common re-reject traps (avoid)

- Message Flow that only says “users opt in by texting us” with no keyword, disclosures, or legal URLs
- Description that only says “budget alerts” with no sender / recipient / purpose
- Sample messages without brand name or STOP language
- Privacy URL behind login (ours is public Funnel — OK)
- Claiming Marketing use case while samples are account alerts
- Website field pointing at MCP root or a login wall — use `/budget-bot/` only
