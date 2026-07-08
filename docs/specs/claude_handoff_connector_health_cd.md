# Claude handoff — connector health C & D (research / build)

**From:** Grok Build session on zaz-astra (2026-07-08)  
**Owner action:** design + implement if feasible; do **not** invent Claude APIs that don’t exist.  
**Related:** [[astra-url-rotation-consumers]], restart-reminder (A), smoke-test/health-check (B).

## Context already built (A + B)

| Layer | What | Status |
|-------|------|--------|
| **A** | After settled restart → in-session alert: check connectors; rotate as needed | Live (`restart-reminder.sh` + SessionStart banner) |
| **B** | Same alert includes **smoke-test** result against **public funnel + `MCP_PATH` from env** (path redacted in alert) | Live (added with this handoff) |
| Hourly | `health-check.timer` emails on sustained funnel failure | Live (separate from restart) |

**Invariant:** Funnel PASS ≠ Claude/Grok connector healthy (their stored URL can be stale after rotate/restart).

## Your job: C and D

### C — True Claude-side connector verification

**Goal:** Detect that the **cloud** Claude connector (interactive claude.ai and/or journaling routine MCP URL) still matches the live box `MCP_PATH`, or at least that it can list tools.

**Constraints:**
- No secret path in git, chat logs, or committed handoffs.
- Prefer fail-open (don’t break restarts if check fails).
- Do **not** pretend an API exists — spike first.

**Spike steps:**
1. Search current Claude / Anthropic docs for any routine/API way to:
   - list MCP connectors for an account, or
   - probe “tools available to routine X”, or
   - read journaling routine config programmatically.
2. If **no API**: document “C is human-only” and propose the best semi-auto alternative (e.g. SessionStart on **cloud** is impossible from the box; optional user paste of connector URL hash for compare).
3. If **partial API** (e.g. only after `/fire` session): fold into D.
4. Deliverable if unbuildable: short design note in memory + this file updated with “C: blocked / no API”.

**If buildable:** smallest script + timer or post-rotate checklist step; email only on clear mismatch/failure (aligned with “email when really broken”).

### D — Post-`/fire` session tool failure detection

**Goal:** After journaling `journal-trigger.sh` gets `post_http=200` + session URL, detect that the **session failed to use Astra tools** (stale MCP URL, authless path wrong, tools missing).

**Spike steps:**
1. Inspect what `/fire` response includes (`claude_code_session_url`, etc.).
2. Check whether session status/logs/transcripts are queryable via any Anthropic API with the routine OAuth / fire token.
3. Practical heuristics if no API:
   - Optional: poll session URL for HTTP shape (likely useless / auth wall).
   - Log session URL always (already done); human checks.
   - **File a product gap** rather than scrape claude.ai HTML.
4. If the journaling **repo** writes a status file or failed-tool marker somewhere the box can see — wire that.

**Deliverable if unbuildable:** document limitations; keep A+B + oauth-48h email as the stack.

## Acceptance

- C and/or D either **shipped small** or **explicitly declined with evidence**.
- No path secrets in repo.
- No email spam for healthy nights.
- Memory fact updated under `/root/memory/` via `memory_*` if MCP available, else careful FS + note.

## Do **not** build

- Full headless Claude Code every 6h as “connector health” (wrong tool for this problem).
- Duplicate health-check email on every restart (restart = in-session; funnel down = health-check email).

## Priority

After any urgent journaling OAuth / rotation work. C/D are **nice-to-have observability**, not blockers for research_fanout.
