# astra-config — zaz-astra rebuild runbook

Config backup + reproducible rebuild for host **zaz-astra** (Debian 13).
Runs the **astra MCP server** (`ad-astra` repo): tools `get_odds`, `grok_x_search`,
`ask_panel`, `get_news_digest`, exposed publicly via Tailscale Funnel as a custom
connector for claude.ai / Grok. (`ask_grok` + `ask_gemini` were merged into
`ask_panel` on 2026-06-14.)

**Target: clone + `setup.sh` + paste 2 keys = working box in ~30 min.**

---

## Rebuild (in order)

```bash
# 1. Clone THIS repo to /root/astra-config
git clone https://github.com/zazesty/astra-config.git /root/astra-config
cd /root/astra-config

# 2. Run setup (idempotent; does everything below up to the key pause)
sudo bash setup.sh
#    - apt: curl, git, tailscale
#    - nvm + node v22.22.3
#    - clone ad-astra -> /root/grok-mcp, npm ci, build, cp kalshi-series.json
#    - recreate 2G /swapfile + swappiness=10
#    - symlink config (system -> repo), enable units, nightly commit timer
#    - tailscale up      <-- INTERACTIVE: open the printed URL, auth the box
#    - tailscale funnel --bg 3000
#    - PAUSE: it tells you to paste the 2 keys, then starts the service

# 3. When it pauses, in another shell:
sudo nano /etc/grok-mcp.env      # fill XAI_API_KEY and GEMINI_API_KEY, save
#    back in setup.sh: press Enter -> service starts
```

## The two keys (the only manual inputs)

| Key | Get it from | Notes |
|-----|-------------|-------|
| `XAI_API_KEY`    | https://console.x.ai | Format `xai-...`. **Set a spend cap.** |
| `GEMINI_API_KEY` | https://aistudio.google.com → "Get API key" | **RESTRICT the key to the Generative Language API** in Google Cloud, or calls are blocked. Set a spend cap. |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys | Format `sk-or-...`. **Required for the default `GEMINI_TRANSPORT=openrouter`** (the live setting); also register `GEMINI_API_KEY` as a BYOK provider key in the OpenRouter dashboard. Set a spend cap. To skip it, run `GEMINI_TRANSPORT=direct` on just the first two keys. |

These live in `/etc/grok-mcp.env` (chmod 600, **never** in git). The first two
alone get you a working box on `GEMINI_TRANSPORT=direct`; the live default
`openrouter` wants the third.

Two *optional* extra inputs power the journaling auto-trigger — the routine
`/fire` URL + token, pasted into `~/.config/journal-trigger/` at step 10. The box
rebuilds fully without them; journaling just won't fire until they're present.
See [Journaling auto-trigger](#journaling-auto-trigger).

## Endpoints

- Public: the `https://<host>.<tailnet>.ts.net` base is whatever `tailscale funnel status` reports; the MCP server's mount path comes from `MCP_PATH` in the off-repo env file (`/etc/grok-mcp.env`).
- Local: `127.0.0.1:3000`. Funnel terminates TLS; the node server is plain HTTP on loopback.

## Rotating the URL (MCP_PATH) — update EVERY consumer

The public endpoint is `<funnel base><MCP_PATH>`. The mount path doubles as the
credential, so we rotate it on purpose — to bust a connector's per-URL tool
cache, or after a leak. (Be sure an API spend limit is in place on every provider
key, so a leaked path can't run up unbounded cost.) **The path is referenced in
several places that do NOT auto-update. Miss one and that consumer silently
breaks** — exactly how the journaling routine stayed dead for a while.

> ⚠️ **After ANY restart: CHECK connectors; ROTATE only as needed.**
> Restarting `grok-mcp.service` can drop live remote MCP sessions (providers often
> do not auto-reconnect). Engine-only restarts do **not** always break every client
> (2026-07-08: Gemini stayed up without rotation). If a consumer is dead, re-add;
> if still stuck (common on Grok), `rotate-url.sh` then re-add all three. **Always
> rotate** when the tool list/schema changes (Grok caches tools per URL). In-session
> reminder: `/root/.grok-mcp-restart.alert` via `grok-restart-reminder.timer` +
> SessionStart `restart-banner.sh` (no routine email).

**Fastest path:** `sudo bash scripts/rotate-url.sh` automates the box side —
generates a fresh path, backs up + rewrites the env, restarts the service, smoke-tests
through the Funnel, and prints the new URL + the consumer checklist below. It bumps the
`/vN` suffix automatically (pass one to override). The cloud reconnects (steps 2–4) are
still manual. The by-hand procedure:

When you change `MCP_PATH` in `/etc/grok-mcp.env`, walk this whole list:

1. **Restart the service** so the new mount takes effect:
   `sudo systemctl restart grok-mcp.service`.
2. **Claude Code journaling routine** — update its MCP connector URL to the new
   path. ⚠️ This one is easy to forget (the URL lives in the cloud routine's
   connector config, not in this repo) and fails *silently* — it just stops
   journaling, no error. **Do not skip.**
3. **Claude interactive connector** (`astra85f`, claude.ai) — reconnect it to the
   new URL, or its tools quietly disappear from your sessions. Also silent.
4. **Grok connector** — reconnect Grok to the new URL. Grok caches its tool list
   *per URL*, so a fresh URL is also how you force it to pick up added/renamed
   tools — often the very reason you're rotating.
5. **`~/.claude/settings.local.json`** — the allowlisted `curl ...` permission
   entries hard-code the path; stale ones only make Claude re-prompt on a manual
   probe (cosmetic — not a silent break).
6. **Verify**: `sudo bash scripts/smoke-test.sh` (discovers the path from the env
   file, so it follows the rotation automatically — a clean run confirms the
   server side; the consumers above are still on you to update).

Never commit the path, or any wording describing the endpoint's auth posture, to git — working tree or history.

## Journaling auto-trigger

Replaces the old once-daily Claude Code on the web *scheduled* trigger with a
usage-gated, POST-driven scheduler on this box. Cron decides **when**; the actual
journaling session still runs in the cloud (billed to the plan), where
`zazesty/Journaling`'s `CLAUDE.md` + SessionStart hooks write the entry. The box
only POSTs the routine's `/fire` webhook.

- **Scripts** — canonical in `home/journal-trigger/`, symlinked into
  `/root/journal-trigger/` (Stow-style; script edits need no re-install):
  - `usage-gate.sh` — reads the OAuth token from `~/.claude/.credentials.json`,
    queries the **undocumented** `api.anthropic.com/api/oauth/usage`. Exit 0 iff
    `seven_day.utilization < weekly_target` **and** `five_hour.utilization < 80`,
    where `weekly_target = 0.5 × hours-into-7day-window` normally, but is lifted to
    a flat **95%** in the **last 5h before the weekly reset** (Sat ~04:00 PT) so the
    otherwise-wasted weekly budget fills ("use it or lose it" — the 5-hr ceiling
    still applies, so it can't spike). Any non-200 (incl. 429) or shape change →
    **fail closed** (skip), no retries.
  - `journal-trigger.sh` — each tick runs the gate; pass → POST `/fire`, fail →
    skip. **No daily floor** — a fully-throttled night legitimately writes
    nothing. Flags: `--dry-run`, `--force` (one-shot end-to-end test, ignores the
    gate). Self-logs to `~/.local/state/journal-cron.log` (one line per tick).
  - `crontab.txt` — hourly **01–06 PT** (`CRON_TZ`), `flock -n` so a slow tick
    never overlaps the next. Installed into the root crontab by `setup.sh` step 8.
- **Secrets** — paste at rebuild step 10, mode 600, **never in git** (they live in
  `~/.config`, outside this repo):
  - `~/.config/journal-trigger/endpoint` — the routine `/fire` URL.
  - `~/.config/journal-trigger/secret`   — its `sk-ant-oat01-…` bearer token.

  Get both by adding an **API trigger** to the `zazesty/Journaling` Routine at
  [claude.ai/code/routines](https://claude.ai/code/routines) (token shown ONCE).
- **Self-throttling is intentional:** each fire raises the usage the gate reads,
  so active nights naturally back off. No extra cooldown is layered on top.

⚠️ This trigger's `/fire` token is a **separate credential** from the MCP
`MCP_PATH` rotation above — `/fire` posts to the fixed `api.anthropic.com`
endpoint, not the funnel. Rotate/revoke it from the routine's API-trigger modal,
not from `/etc/grok-mcp.env`. (The README's MCP_PATH step about the "journaling
routine connector URL" is a *different* thing: the cloud routine's MCP connector
for the astra tools, unrelated to firing the routine.)

## Drift guard

A daily check (`scripts/drift-check.sh`, user timer `drift-check.timer` at 05:30 PT)
that asserts the box still reproduces from this repo, and drops a sentinel the
**SessionStart banner** (`drift-banner.sh`) surfaces into the Claude session — so the
assistant notices unreproduced state and offers to mirror it in. (This is the gap
that left the journaling trigger un-backed-up until it was wired in.) It's
Claude-facing on purpose; grok-mcp manual-backup hygiene stays operator-facing in
`warn-uncommitted.sh` — the same split as the [backup model](#backup-model).

Checks (config-completeness only — grok-mcp push state is deliberately *not* here):

1. Tracked symlinks still resolve into the repo (broken / repointed / missing source).
2. Every enabled systemd user timer / custom system unit has a tracked unit file.
3. The live root crontab matches `home/journal-trigger/crontab.txt`.
4. Every live `/etc/grok-mcp.env` key exists in `.env.example` (names only, never values).

Intentional, not-yet-mirrored drift? `bash scripts/drift-check.sh --ack` records the
current finding-set as accepted and silences the banner until the finding-set changes.

## Verify

`setup.sh` runs this automatically as its final step (`scripts/smoke-test.sh`) and
**fails the rebuild** if the funnel doesn't serve the expected 10 tools. It retries
for ~45s because Funnel can take a few seconds to come live after `tailscale up`.
Run it anytime:

```bash
sudo bash scripts/smoke-test.sh
# discovers the funnel URL from `tailscale funnel status`, calls tools/list,
# asserts EXPECTED_TOOLS (default 10): get_odds, ask_panel, grok_x_search, get_news_digest, ask_oracle, get_metrics + 4 memory_*
# reads the mount path from MCP_PATH in the off-repo env file (/etc/grok-mcp.env)
# tunables: EXPECTED_TOOLS, MCP_PATH (override), RETRIES, SLEEP_SECS, FUNNEL_URL
```

Or by hand:

```bash
BASE="$(tailscale funnel status | grep -oE 'https://[^ ]+' | head -n1)"
MCP_PATH="$(grep -E '^MCP_PATH=' /etc/grok-mcp.env | cut -d= -f2- | cut -d, -f1)"
curl -s "$BASE$MCP_PATH" -X POST \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# expect 10 tools (6 original + memory_search/retrieve/upsert/list)
```

## Gotchas that cost hours

These are documented as a runtime-debugging index; several are also auto-handled
on a clean rebuild (tagged below) — the tag means "setup.sh does this for you,"
not "ignore it when debugging a live box or a manual redeploy."

- **nvm node is not on `sudo`'s PATH.** systemd/sudo must use the absolute path
  `/root/.nvm/versions/node/v22.22.3/bin/node`. *(included in setup.sh — the unit
  already hard-codes it.)*
- **`tsc` does not copy `kalshi-series.json`.** After every build, `cp
  src/kalshi-series.json build/` or `get_odds` breaks. *(included in setup.sh on
  rebuild — but NOT on a manual `tsc` redeploy, where you must do it yourself.)*
- **Gemini key must be RESTRICTED to the Generative Language API** or every
  Gemini call (now via `ask_panel`) 403s. *(setup.sh prompts for this; it can't set it for you.)*
- **Tailscale cert 500 after toggling HTTPS/DNS in the admin console:** run
  `sudo systemctl restart tailscaled` to force a netmap refresh, then retry.
  *(included in setup.sh on rebuild; recurs at runtime whenever you toggle the
  admin console.)*

The Grok per-URL tool-cache gotcha now lives in **Rotating the URL (MCP_PATH)**
above (step 4), since rotating is the fix.

## Memory auto-update harvester

Reliable backstop for the shared `/root/memory/` KB: a **systemd user timer**
(`memory-harvest.timer`, hourly at :07 PT) runs `scripts/memory-harvest.mjs`, which
scans Claude session transcripts, substance-gates for real work (edits/deploys/decisions),
extracts 0–3 high-confidence facts via OpenRouter (`gemini-3.1-flash-lite`), and upserts
through the local `memory_upsert` MCP tool (which auto-regenerates `MEMORY.md` +
`index.md`).

- **State cursor** — `/root/.local/state/grok-mcp/memory-harvest.json` (outside the repo
  to avoid auto-commit churn).
- **Dry-run first** — `MEMORY_HARVEST_DRYRUN=1 node scripts/memory-harvest.mjs` writes
  candidates to `harvest-dryrun.log` without upserting. Flip live by running the timer
  without the env var (or drop it from the unit `Environment=` when ready).
- **Conflicts** — flagged with `needs-review` tag + Resend alert; never silent overwrite.
- **Requires** — `OPENROUTER_API_KEY` in `/etc/grok-mcp.env`; `grok-mcp.service` running
  on loopback. No URL rotation (no new MCP tools).

## What's NOT in this repo (by design)

- `/etc/grok-mcp.env` — secrets.
- `~/.config/journal-trigger/{secret,endpoint}` — journaling `/fire` token + URL (mode 600).
- `/root/*.crt /*.key` — Tailscale-managed cert state (Funnel re-issues its own).
- `/swapfile` — recreated by setup.sh, not stored.
- `node_modules/`, `build/` — rebuilt from the app repo.

## Layout

```
etc/systemd/system/grok-mcp.service   # the service unit (symlinked into place)
etc/sysctl.d/99-swap.conf             # vm.swappiness=10
home/.config/systemd/user/            # nightly auto-commit service + timer
home/.claude/settings.json            # Claude Code permissions + SessionStart hook (symlinked into place)
home/.bashrc                          # operator shell rc (interactive warn snippet + nvm); symlinked into place
home/journal-trigger/usage-gate.sh    # journaling usage gate (OAuth /usage; fail-closed, no retries)
home/journal-trigger/journal-trigger.sh  # gated /fire POST scheduler (symlinked into /root/journal-trigger/)
home/journal-trigger/crontab.txt      # hourly 1-6am PT root crontab (installed by setup.sh step 8)
scripts/commit-if-changed.sh          # commit repo iff dirty (hook + timer use it)
scripts/push-if-ahead.sh              # push to origin iff local is ahead (nightly timer only; off-box backup floor 24h)
scripts/warn-uncommitted.sh           # ~/.bashrc interactive reminder: warn if grok-mcp has uncommitted changes
scripts/smoke-test.sh                 # curl funnel + assert tool count; setup.sh's final self-check (retries while Funnel warms up)
scripts/drift-check.sh                # daily: assert the box still reproduces from this repo; drops a sentinel on drift
scripts/drift-banner.sh               # SessionStart hook: surface the drift sentinel into Claude's context
scripts/memory-harvest.mjs            # hourly: extract memory deltas from Claude transcripts → MCP upsert
home/.config/systemd/user/memory-harvest.{service,timer}  # harvester schedule (:07 PT)
.githooks/pre-commit                  # aborts commits containing key-shaped strings
.env.example                          # template for /etc/grok-mcp.env
setup.sh                              # idempotent rebuild
```

## Backup model

**Why the two repos have opposite policies:** this repo is **config** — edits are
small, rare, and complete-on-save, so an auto-commit always captures a coherent
state; auto-push is safe and desirable. `grok-mcp` is **live source under active
edit** — auto-push would force an auto-commit on whatever happens to be on disk,
which routinely means a half-finished mid-edit tree (broken build, dangling
refactor) snapshotted into history. So config auto-saves; source is committed by
hand at known-good points.

**astra-config (this repo) — auto-backed-up (config changes rarely, so every snapshot is coherent):**

- **Auto-commit (local):** the **SessionStart Claude hook** (`~/.claude/settings.json`,
  itself symlinked into this repo so it's captured) runs `commit-if-changed.sh` — commits
  this repo iff dirty. The **nightly user timer** (`astra-commit.timer`, **3am
  America/Los_Angeles**, DST-aware) is the floor.
- **Auto-push (off-box) — Option A, NIGHTLY:** the nightly timer runs `commit-if-changed.sh`
  **then** `push-if-ahead.sh` (2nd `ExecStart`), which pushes to `origin` only when local
  is ahead. **Off-box backup floor: 24h.** The SessionStart hook is **commit-only** — only
  the nightly path pushes. Auth: `credential.helper=store` + a `github.com` token in
  `~/.git-credentials` (user `zazesty`, set by `setup.sh` step 6).
- **Push failures are never silent:** every run appends to **`/root/.astra-push.log`**; a
  failure also drops **`/root/.astra-push.failed`**, which the `~/.bashrc` login warn net
  (`warn-uncommitted.sh`) surfaces on the operator's terminal until the next clean push.
- Force an immediate off-box backup: `git -C /root/astra-config push`.

**grok-mcp (the app) — NOT auto-backed-up, by design:**

- **Why manual:** it's a clone of upstream `ad-astra` *and* it's source under active edit.
  Wiring it into the nightly auto-push would force an auto-commit of the on-disk tree,
  which is frequently a broken mid-edit state (failing build, partial refactor) — that
  belongs nowhere in history. So it's backed up with **manual commit + push via its own
  flow**, committed only at known-good points. The `~/.bashrc` login warn net (`warn-uncommitted.sh`, interactive shells only,
  prints to the operator's terminal — **never** into Claude's context) nags whenever it
  has uncommitted work.
