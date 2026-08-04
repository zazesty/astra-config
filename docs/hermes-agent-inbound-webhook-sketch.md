# Sketch: inbound wake (Twilio SMS → box) for a future Hermes **Agent**

> **Not Hermes-Finance.** Finance = rules/cron. This is a hypothetical always-addressable co-admin agent.

## Goal

From the couch: text a number → box receives webhook → agent run is **queued or started** → optional SMS reply.

## Minimal receive path (draft)

```
iPhone SMS
  → Twilio number
  → HTTP POST https://<funnel-host>/<secret>/twilio/sms
  → small systemd service on 127.0.0.1
  → validate Twilio signature
  → append JSON line to ~/.local/state/hermes-agent/inbox.jsonl
  → optional: systemctl start hermes-agent-once.service
  → TwiML response (empty or "queued")
```

### `inbound-sms` handler (pseudo)

```python
# POST form: From, Body, MessageSid
# 1. verify X-Twilio-Signature with auth token
# 2. write {ts, from, body, sid} to inbox.jsonl (mode 600)
# 3. if body matches wake words or always: touch wake flag / start oneshot
# 4. return 200 + TwiML <Response></Response>
```

### Funnel

- Reuse Tailscale Funnel **HTTPS** (already required for Plaid-style public HTTPS).
- Path must be **secret** (same class as MCP_PATH) — auth is signature + unguessable path.
- Never commit path.

### What “wake” means (choose one)

| Mode | Behavior | Cost |
|------|----------|------|
| **A. Inbox only** | Next Grok Build / agent session reads inbox | Near zero continuous |
| **B. Oneshot job** | `agent-run hermes-agent-once` with Body as prompt, reply via Twilio | Per-text LLM $ |
| **C. Always-on agent** | Long-lived process poll inbox / websocket | RAM + idle $ + complexity |

**Recommended first:** A or B — not C.

## Continuous Hermes Agent (not finance) — inefficiency

| Cost center | Impact on this ~4 GB box |
|-------------|---------------------------|
| Always-loaded model session | High $ if cloud; impossible for big local models |
| Idle process + tools | ~100–400 MB + ops risk |
| Context growth | Re-squirrely long before 500k unless compacted |
| Duplicate of Grok Build | Two co-admins unless Hermes *is* the only door |

**Verdict:** continuous presence is **possible** but **poor fit** as 24/7 LLM. Better: **event-driven presence** (SMS/webhook/timer → oneshot with memory + tools → reply). Feels awake from the couch; sleeps otherwise.

## iOS silent mode + Twilio

Yes: set the Twilio number as a **Focus / Emergency Bypass** contact (or known sender) so SMS can alert through silent/Focus — similar spirit to Pushover emergency, but configured on the phone per-contact, not in Twilio priority bits.

Pushover emergency is server-side priority; SMS punch-through is **iOS contact/Focus settings**.
EOF