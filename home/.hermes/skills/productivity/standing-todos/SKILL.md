---
name: standing-todos
description: "Use when parking work or listing open box todos. Add via standing-todos.sh; do not implement."
version: 1.0.2
author: zaz-astra
platforms: [linux]
metadata:
  hermes:
    tags: [todos, standing, park, photon]
    category: productivity
    related_skills: [photon-imessage, budget-bot]
---

# Standing todos (park, don't ship)

Open work board for the box. Grok Build reads this later. Not system status.

```bash
/root/astra-config/scripts/standing-todos.sh list
/root/astra-config/scripts/standing-todos.sh add "title" --author "Hermes" --owner agent --priority 3 --next "Grok Build: …"
/root/astra-config/scripts/standing-todos.sh note <id> "text"
/root/astra-config/scripts/standing-todos.sh done <id>
```

`--author` is required: sign as Hermes when you write the task; it is separate from ownership. Never guess who wrote an unsigned legacy task.

`--owner` is who acts next: `agent` | `human` | `either`. Default `agent` when parking implementation.

## When to add (default)

Any Photon ask that would change code, config, templates, skills, notify copy, or bot policy — including "please change X" and "if lengthy, make a note." **Park first. Do not read the repo to size it.**

## When not to add

- One known CLI already answers (budget-status, plaid-status, this list)
- A trivial one-step reply leaves no durable or open work
- User only wanted a capture into Astra
- Duplicate of an already-open item (`list` first if the title might already exist)

## Track work through completion

Use this board for multi-step durable or open work. Keep the existing `open` / `done` status; record progress with the existing `note` command, not a second board or new status fields.

- **Capture:** sign the author, give an actionable `--next`, and note `[acceptance]` followed by the observable outcome that counts as success. A new capture may be `[phase: proposed]`; if acceptance or the next step is unclear, record what needs clarification rather than inventing it.
- **Implement:** the implementing box agent records `[phase: implemented]` with what changed. It adds `[verification]` notes with the checks actually run, their observed results, and any remaining limits; then `[phase: verified]` when required checks pass.
- **Ship:** when a deploy or release applies, the implementing box agent records its completion and the relevant live check in a `[phase: shipped]` note. Implementation alone does not establish verification or release.
- **Wait:** if user input, a reconnect, or user acceptance is still needed, keep the item open and note `[phase: awaiting-user]` with the exact remaining action. Otherwise retain the latest phase supported by evidence.
- **Close:** use `done` only after acceptance is met, required verification passes, and every applicable deploy/release is complete. Do not backfill phases or evidence for legacy items from guesses.

```bash
/root/astra-config/scripts/standing-todos.sh note <id> "[acceptance] Describe the observable success condition."
/root/astra-config/scripts/standing-todos.sh note <id> "[phase: proposed] Next: describe the first actionable step."
```

This bookkeeping does not expand Hermes's authority: Hermes parks substantive work for the box agent, and does not investigate or ship it unless the user explicitly overrides that rule. Verification and release notes belong to the implementing box agent.

## How to reply

One line: parked as `<title>`. No investigation recap.

## Pitfalls

1. Opening files to "see if it's small" — that is the expensive path; park instead.
2. Using Hermes-native todos / MEMORY.md as a second board.
3. Marking done from Photon unless he confirms it shipped.
4. Parking a NorCal Plaid scrub or a re-link. The Item was removed 2026-09-29. Do not re-add either task. The September statement closes the books.
