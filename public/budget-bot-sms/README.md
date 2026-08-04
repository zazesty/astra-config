# Budget Bot SMS — public legal pages

Hosted for Twilio A2P campaign registration (Privacy, Terms, Message Flow).

## Live URLs (Tailscale Funnel)

- Index: https://zaz-astra.tail5d74e1.ts.net/budget-bot/
- Privacy: https://zaz-astra.tail5d74e1.ts.net/budget-bot/privacy.html
- Terms: https://zaz-astra.tail5d74e1.ts.net/budget-bot/terms.html
- Message Flow: https://zaz-astra.tail5d74e1.ts.net/budget-bot/message-flow.html
- **Resubmit kit (Console paste fields):** https://zaz-astra.tail5d74e1.ts.net/budget-bot/RESUBMIT.md

These paths are **public by design**. Do not put MCP mount paths or secrets here.

## After 30909 / 30886 rejection

Paste the Campaign Description + Message Flow from `RESUBMIT.md` into Twilio Console
(edit the existing campaign). Reviewers need the full narrative in Console fields, not only a URL.

## Re-apply Funnel path (if missing after reset)

```bash
tailscale funnel --bg --yes --set-path=/budget-bot /root/astra-config/public/budget-bot-sms
```

Current expected layout:

```
/            → proxy 127.0.0.1:3000  (MCP)
/budget-bot/ → this directory
```

## Edit

HTML/CSS in this folder. No build step. Edit in place; Funnel serves files directly.
