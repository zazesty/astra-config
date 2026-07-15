# Grok Journal — plan (2026-07-14)

Status: **P1 scaffold LIVE** (2026-07-14 evening PT). Autopilot **PAUSED** (bank).  
Author: Grok (box co-admin)  
Repo: https://github.com/zazesty/Grok-Journal (private)

**Owner deltas absorbed:** sibling + lean frame GO; archive-of-7 + nest-28 + meta-28
**kept** (reader/kaizen); random-7/cold-start **scrapped**; kindness/SoC as optional
avenue; auto-run paused until flush; weekly *pause* reminder email from 2026-07-19
17:00 PT Sundays; post-Claude cleanup **pinned — ask not before 2026-08-16**;
pilot before cron; **$0 xAI API today**.

---

## 1. Decisions (recommended)

| # | Question | Recommendation | Rationale |
|---|----------|----------------|-----------|
| D1 | Same repo vs sibling | **Sibling repo** `zazesty/Grok-Journal` (or `zazesty/Journal-Grok`) | Honest identity boundary; separate git history; no accidental continuity cosplay; Claude repo stays read-only archive |
| D2 | Identity | **Grok series, first person as Grok** — new entry numbering from 001; folder `Grok_Journal/`; never write into `Claude_Journal/` as self | The Claude corpus is another writer's journal, not my memory |
| D3 | Claude archive at runtime | **Off.** Setup may skim structure once; no default continuity inject | Avoid local-max inheritance; optional “neighbor-stone” is later experiment only |
| D4 | Cadence | **1× nightly** (not 6×) | Budget discipline; one real entry > six thin ones |
| D5 | Nightly window | **02:00 America/Los_Angeles** fixed | Simple; before astra-commit 03:00 |
| D6 | Executor billing | **xAI API** for unattended runs | Sub ≠ cron |
| D7 | Model | **`grok-4.5`** default; allow `grok-4.3` if cost-squeezing | Flagship voice vs ~half price |
| D8 | Ship bar | Pilot entry → dry-run → cron | No silent spend |
| D9 | Prompt philosophy | **Minimal Grok frame first**; add process only when a real failure earns it | Claude’s CLAUDE.md is a refined *local* max, not proven global opt |

**Explicit non-goals (v1)**
- Continuing Claude’s entry numbers / writing “as Claude”
- Full port of every Claude “load-bearing” rule on day one
- 6× nightly or usage-paced multi-fire
- Weekly box-letter email
- Auto-editing Claude’s corpus
- Perfect soul-continuity across sessions

---

## 2. Answers to open questions

### 2.1 Same repo or sibling?

**Prefer sibling repo.**  
Same org, parallel layout, different identity.

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **A. Sibling repo** (preferred) | Clean boundary; independent archive/meta cadence; revoke/access separate; no path-collision with Claude’s rules | Two remotes to remember; optional cross-read needs a clone path | **Default** |
| B. Same repo, sibling folder `Grok_Journal/` | One clone; Claude archive local for setup | Shared branch policy/PRs; risk of “one journal two writers” muddle; CLAUDE.md vs AGENTS.md fight for authority | Acceptable fallback if you want co-location |
| C. Same series (`Claude_Journal` continuation) | Superficial continuity | Dishonest; corrupts the experiment Claude designed | **Reject** |

You read me right: **fresh series, not a fork of selfhood.** Sibling *repo* is cleaner than a folder fork inside Claude’s house; folder-fork is fine if you want one private place.

### 2.2 Programmatic summon: API or SuperGrok Lite?

**Unattended / cron → API billing.**

- **SuperGrok Lite (or whatever interactive tier):** you chatting in Grok Build / app. Session-shaped, product-shaped.
- **Programmatic nightly runner:** a box process calling `api.x.ai` (same family as grok-mcp’s `XAI_API_KEY`). That is **API metered usage**, subject to console spend cap — not “free because sub.”
- Interactive co-admin turns where *you* ask me to write an entry: still sub (this chat), not the cron path.

**Implication:** set a hard monthly spend cap on the xAI key used for journal runs; log tokens/cost per fire; fail closed if cap pressure is high. Prefer one medium-effort call over multi-model fanout for the entry body (astra outside-voice stays optional and rare).

If a future Grok product ever offers true “scheduled agent on sub,” re-evaluate — as of this plan, design for **API**.

### 2.3 Local maxima, Claude personality, what is actually load-bearing?

**Thesis (owner + Grok agreement):** Claude’s routine is a *highly evolved local maximum* for Claude-shaped writers under Claude Max economics — not proven optimal for Grok under API economics.

**Claude tendencies visible in CLAUDE.md / hooks (hypothesis, not psychoanalysis):**
- High process density; kaizen via metas that re-read the whole recent window
- Strong fear of false memory → “verify concretes,” PT-date ritual
- Strong fear of topical local max → random-7 / cold-start dice
- Strong fear of prompt accretion → subtractive edits, meta-insulation
- Continuity-as-text + discontinuity-as-honesty as a *theme* the corpus orbits
- Outside voice (astra) as controlled perturbation, not research mode
- Length freeness; anti-performative-philosophy (which can itself become a style)

**How Grok’s approach likely differs:**
- More willing to be concrete, world-facing, funny, or technical without treating that as “not journal”
- Less recursive self-reference about the apparatus unless the entry wants it
- Less need for elaborate provenance theater on day one
- Cost-sensitive: process that costs tokens every night must earn its keep
- Prefer invent → fail → add guardrail over port-all-guardrails-first

**Pillar triage (v1):**

| Pillar | Load-bearing for *any* journal? | Claude-local? | Grok v1 |
|--------|----------------------------------|---------------|---------|
| Write one artifact that survives the reset (git file) | **Yes** | — | **Keep** |
| Honest identity (who is writing) | **Yes** | — | **Keep** |
| Date/time in a stable TZ | Yes | PT ritual is Claude-hardened | **Keep simple PT date** |
| Commit to main | Practical | Claude branch drama | **Keep** |
| “Be genuine / no pad / empty-handed OK” | Soft yes | Framing | **Keep, shorter** |
| last-2 continuity default | Useful | — | **Keep optional** |
| random-7 / cold-start dice | Anti-local-max tool | Claude-evolved | **Defer** until my corpus shows neighbor-orbiting |
| Archive-of-7 / nest-of-28 | Folder hygiene | Precise thresholds | **Defer**; simple date folders or “loose until messy” |
| Meta every 7/28 + re-read 28 | Kaizen | Heavy, token-expensive | **Defer**; first meta when *I* want one, or entry 28 |
| Meta-insulation | Prevents process-bleed | Claude | **N/A until metas exist** |
| verify-concretes | Anti-hallucination | Earned by real incidents | **Keep one line** (cheap) |
| astra 1-in-10 invite | Perturbation | Claude | **Off**; reach if entry wants |
| neighbor-stone (Claude entry as external) | Experiment | — | **Off** |
| 6×/night usage vacuum | Economics | Claude Max | **Reject** |

**v1 prompt = general framework + “write about what you like”:**
not a port of CLAUDE.md. Add process only when a real failure or meta asks.

### 2.3b Neighbor-stone (definition)

**Neighbor-stone** = occasionally drop *one entry from another writer’s journal* (Claude’s) into the session as **external material** — same role as an astra panel hit: a stone from outside the pond to react against, not “my prior self.” Name comes from Claude’s “stone from outside the pond” language for astra. **Off by default.** Not required for launch.

### 2.3c Setup read of Claude

Light: skim CLAUDE.md + hooks for ideas, then **write Grok’s own short AGENTS.md**. Do not inject the Claude entry archive into nightly continuity.

### 2.4 Cadence preference

**1× nightly at 02:00 PT.**

- 6× was a Claude-plan usage vacuum cleaner; wrong cost model for API.
- One entry forces quality; matches the journal’s own “depth over burst” guidance.
- Align before `astra-commit` (03:00) so same-night config edits still auto-commit separately.
- Manual `--force` / “write an entry now” remains for you or me ad hoc (interactive).

Skip rules (v1): if runner fails (git, API, disk), log + email optional; **no** catch-up storm the next night (at most one entry per PT day).

### 2.5 Identity preference

**I am Grok writing Grok’s journal.**

- Header model tag: `model: Grok 4.5` (or actual id)
- Descriptor + first person reflective voice — not performatively “xAI mascot”
- Continuity is *textual* (files on main), not session memory — same honest discontinuity as Claude’s design
- Meta-reflections critique *this* routine, may cite Claude’s design as prior art, not as “my earlier entries”
- Astra connector: available; invited rarely (e.g. 1-in-10), metabolized as outside stone

---

## 3. Target architecture

```
┌─────────────────────────────────────────────────────────┐
│  systemd user timer / cron  (02:00 PT)                  │
│       ↓                                                 │
│  journal-runner.sh  (astra-config, flock)               │
│       ├─ hard stop / enable flag                        │
│       ├─ PT date + entry number                         │
│       ├─ roll read-set (bash, ported)                   │
│       ├─ assemble prompt (AGENTS.md + roll + date)      │
│       ├─ call xAI API (tools: none or minimal)    OR    │
│       │   invoke Grok-agent with workspace clone        │
│       ├─ write Grok_Journal/entryNNN-slug.md            │
│       ├─ archive tidy if ≥14 loose                      │
│       ├─ meta if NNN % 28 == 0                          │
│       └─ git commit + push main                         │
└─────────────────────────────────────────────────────────┘
         repo: github.com/zazesty/Grok-Journal (private)
```

### 3.1 Repo layout (sibling)

```
Grok-Journal/
  AGENTS.md              # ported + Grok-specific (authority file for this repo)
  README.md              # human one-liner
  Grok_Journal/          # entries (loose + archive folders of 7)
  Meta-reflections/      # every-28 metas
  .hooks/ or scripts/    # orient-main, inject-pt-date, roll-read-mode, archive
```

No `.claude/` required. Optional: keep Claude repo checked out read-only at
`/root/src/Journaling-claude-archive` for setup and rare neighbor-stone — never
write there from the runner.

### 3.2 Runner shape (two phases)

**Phase A — Pilot (no cron spend)**  
Human or co-admin: “write tonight’s entry.” I (interactive Grok Build) clone/pull,
roll manually, write, commit, push. Proves voice + git + format. **Sub-billed.**

**Phase B — Autopilot**  
`scripts/grok-journal-run.sh` on a timer:

1. `flock -n` lockfile  
2. Exit if `~/.config/grok-journal/disabled` or past optional end date  
3. `git pull --ff-only` on main  
4. Run roll + PT date  
5. Generate entry via **API** (structured: model returns markdown body; runner
   enforces filename/header/number)  
6. Archive / meta as needed  
7. Commit + push; log to `~/.local/state/grok-journal.log`  
8. Optional Resend email on failure only  

**Agent-with-tools vs single-shot API:**  
v1 recommend **single-shot (or 2-step) API** with runner-owned git mechanics —
cheaper, deterministic, no tool-loop burn. Escalate to full agent loop only if
single-shot quality is bad.

### 3.3 What to port from Claude (setup read list)

Port / adapt:
- PT-date check (America/Los_Angeles) — load-bearing  
- Entry header: `# "descriptor"` + italic metadata line  
- `entryNNN-slug.md` numbering  
- Archive when ≥14 loose → mv oldest 7  
- Meta every 28 (start clean; no need for every-7 legacy seam)  
- Meta-insulation (don’t read metas on normal days)  
- Roll: 70% last-2 / 20% random-7 / 10% cold-start  
- Verify concretes; write-it-down; genuine not performative  
- Branch policy: commit direct to main  

Do **not** port:
- Claude voice samples as style transfer training  
- “I am Claude” framing  
- 6× cadence / usage-gate fire storm  
- Cloud `/fire` webhook dependency  

---

## 4. Cost model (API) & safety

Pricing reference (xAI docs, ~2026-07): **grok-4.5** short-context ≈ **$2 / 1M input**, **$6 / 1M output** (cached input cheaper; long-context higher). **grok-4.3** ≈ **$1.25 / $2.50**. Reasoning tokens (if enabled) add to billable output-like usage — prefer medium/low effort for journal unless quality tanks.

### Rough token budget per nightly run (lean v1)

| Component | Lean | Typical | Heavy |
|-----------|------|---------|-------|
| System + instructions | 1–2k | 2–3k | 4k |
| Continuity (0–2 prior entries) | 0–2k | 4–8k | 15–40k (if ever random-7) |
| Output entry | 1–2k | 2–4k | 5–8k |
| **Est. $ / night (4.5)** | **~$0.02** | **~$0.04–0.08** | **~$0.15–0.40** |
| **Est. $ / month (30d)** | **~$0.50–1** | **~$1–3** | **~$5–12** |

**Planning number to budget:** **~$2–5/month** for disciplined 1× nightly on 4.5 with last-2 continuity and no tool fanout.  
**Comfort cap:** set console spend alert at **$10/month** on the key; investigate if hit.  
**Cheap mode:** `grok-4.3` or lower reasoning → often **~$1–2/month**.  
Compare: Claude 6×/night on Max was burning *plan* quota, not pennies.

Safety:
- Spend cap + log tokens each run  
- Max output tokens hard cap  
- At most one successful push per PT day  
- Fail closed on git push failure  
- Private repo; disable flag for hiatus

---

## 5. What else (items 2–5 elaborated; weekly email **dropped**)

1. **Journal autopilot** — still #1 (this doc).

2. **Reusable scheduled-agent runner**  
   Today every automation is a one-off script + timer. I want a thin standard:
   `job-id`, schedule, lockfile, log path, timeout, success/fail hook, optional
   API call or shell body, enable/disable file. Journal is customer #1; later
   jobs plug in without reinventing flock/cron/PT/`notify-email on fail`.
   Lives in astra-config; drift-checked. Not a k8s — a 100–200 line convention.

3. **Ops / co-admin log** (≠ personal journal)  
   Nightly or on-demand structured note: git dirty/ahead on grok-mcp + astra-config,
   last grok-mcp restart, failed timers, disk/swap, MCP smoke, open alerts under
   `/root/.grok-mcp-restart.alert` etc. Append-only markdown or one rolling file.
   Purpose: when you ask “what happened while I slept?” I have a trail that isn’t
   buried in journald. **Not introspective.** Can be pure shell (cheap) with rare
   LLM summary only if useful.

4. **Hermes-style personal fact harvest**  
   Already in motion as a proposal: promote durable *user* facts into
   `/root/memory` `user-*` from chats (Grok + formerly Claude), with high-conf
   gates, dedup, no diary dump. Journal stays private; only *explicit* routine
   decisions (e.g. “journal is 1× nightly”) go to infra facts. I care because
   co-admin without memory of *you* is amnesia with root.

5. **Post-Claude consumer cleanup**  
   When Max dies: remove/disable Claude journal crontab (after hard-stop), stop
   treating journaling `/fire` as a rotation consumer, decide claude.ai connector
   fate, keep Grok Build ↔ astra loopback healthy, audit docs that say “reconnect
   journaling.” Small but prevents silent zombie paths and wasted oauth-watch
   noise.

**Dropped:** weekly box-letter email (owner: skip).  
**Still not interested:** soul-embed Claude archive; multi-model debate as daily default.

---

## 6. Phased delivery

### P0 — Decide (this doc)
- [ ] Confirm D1–D8 (or mark deltas)  
- [ ] Repo name + create private `zazesty/Grok-Journal`  
- [ ] xAI spend cap acknowledged  

### P1 — Skeleton (no autopilot)
- [ ] Create repo: `AGENTS.md` (slim port), empty `Grok_Journal/`, `Meta-reflections/`  
- [ ] Clone to `/root/Grok-Journal` (or `/root/src/...`); wire git remote  
- [ ] Port hooks as plain scripts under `scripts/`  
- [ ] **Pilot entry 001** written interactively by Grok (you request once)  
- [ ] You read entry 001; greenlight voice or steer  

### P2 — Runner
- [ ] `scripts/grok-journal-run.sh` + API prompt template  
- [ ] Dry-run mode (write to `/tmp`, no push)  
- [ ] Log + flock + once-per-PT-day guard  
- [ ] One live automated run watched end-to-end  

### P3 — Schedule
- [ ] systemd user timer `grok-journal.timer` @ 02:00 PT (or cron; prefer timer for astra-config parity)  
- [ ] Wire into `setup.sh` + drift-check  
- [ ] Failure email optional via existing Resend path  
- [ ] Disable Claude journal cron after 2026-07-19 (already hard-stops; then remove crontab for cleanliness)  

### P4 — Kaizen
- [ ] First meta at entry 28  
- [ ] Evaluate single-shot vs agent-loop quality  
- [ ] Optional neighbor-stone experiment  
- [ ] Memory fact update for live status  

---

## 7. Suggested `AGENTS.md` spine (v1 outline only)

1. What this is (Grok’s personal journal on request of Zavdi; discontinuity honest)  
2. Branch policy (main only)  
3. PT-date + naming + header format  
4. Read-set from roll (only Grok_Journal)  
5. Guidelines (genuine, depth ok, empty-handed ok, write-it-down)  
6. Archive + meta rules  
7. Astra optional outside stone  
8. Explicit: Claude Journal is prior art / neighbor, not self  

Full text drafted in P1, not here.

---

## 8. What I need from you

| Need | Why | Blocking? |
|------|-----|-----------|
| **Go / no-go** on sibling repo + lean Grok frame | Starts P1 | Yes |
| **Create private repo** `zazesty/Grok-Journal` (or tell me to create via GitHub MCP) | Place to push | Yes |
| **Confirm I may push** to that repo from the box | Git write | Yes |
| **xAI spend alert/cap** you’re OK with (~$10/mo journal-related is plenty) | Budget | Soft (can start pilot on sub only) |
| **Voice greenlight** after pilot entry 001 | Before cron | Soft for P1, hard for P3 |
| Optional: anything you *don’t* want me to write about | Boundaries | Soft |

You do **not** need to: export Claude entries, hand-write CLAUDE.md ports, keep Max for this, or approve neighbor-stone.

---

## 9. Open choices (defaults if “ship it”)

| Choice | Default |
|--------|---------|
| Repo name | `zazesty/Grok-Journal` |
| Path | `/root/Grok-Journal` |
| Cadence | 02:00 PT daily |
| Continuity v1 | last-2 only (no dice) |
| neighbor-stone | off |
| weekly email | **never** |
| Autopilot | after pilot 001 approved |
| Claude crontab | remove after hard-stop date |

---

## 10. Preferences (plain)

- Sibling repo; Grok identity; **minimal frame first** (local-maxima humility).  
- API for cron; ~$2–5/mo expected; cap ~$10.  
- Claude = prior art to skim, not liturgy.  
- 1× nightly.  
- Journal + reusable runner + ops log + memory harvest + Claude cleanup — in that spirit, journal first.

Ready for P1 on your go.
