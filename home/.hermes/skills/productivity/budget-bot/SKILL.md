---
name: budget-bot
description: "Budget status: calendar + rolling via Budget Bot CLI."
version: 1.1.0
author: zaz-astra
platforms: [linux]
metadata:
  hermes:
    tags: [budget, finance, pace, hardcap]
    category: productivity
    related_skills: [standing-todos, budget-bot-ops]
---

# Budget Bot status

When Zavdi asks **budget status**, how spending is going, pace, hardcap left, calendar vs rolling month — run **only** this CLI (do **not** invent numbers). Do not search Astra, read templates, or explore the repo. One command, paste both lines, stop.

Copy / policy / code changes → skill **standing-todos** (park). Do not implement from Photon.

```bash
cd /root/budget-bot && python3 -m budget_bot budget-status
```

Optional as-of date:

```bash
cd /root/budget-bot && python3 -m budget_bot budget-status --as-of 2026-08-07
```

## How to reply

Paste the CLI output. One short sentence of color is fine; no lecture.

- The line is the rolling pace count: `{N} days ahead of pace` or `{N} days under pace`. Same sentence as Pushover. No percent of cap, no Overall average, no separate calendar line.
- A cash-vs-bills line may follow when material dues are in the horizon.
- Do not dump raw `txns.json` or tokens into chat.

## JSON deep dive

```bash
cd /root/budget-bot && python3 -m budget_bot status
```

## Not this skill

Plaid Link URLs, env secrets, or pushing live notify — escalate to Grok Build / co-admin on the box.
