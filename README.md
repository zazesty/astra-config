# astra-config — zaz-astra rebuild runbook

Config backup + reproducible rebuild for host **zaz-astra** (Debian 13).
Runs the **astra MCP server** (`ad-astra` repo): tools `ask_grok`, `get_odds`,
`grok_x_search`, `ask_gemini`, exposed publicly via Tailscale Funnel as a custom
connector for claude.ai / Grok.

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

Both live in `/etc/grok-mcp.env` (chmod 600, **never** in git).

## Endpoints

- Public: the `https://<host>.<tailnet>.ts.net` base is whatever `tailscale funnel status` reports; the MCP server lives at `/mcp` (and `/mcp/PATH` alias).
- Local: `127.0.0.1:3000`. Funnel terminates TLS; the node server is plain HTTP on loopback.

## Verify

`setup.sh` runs this automatically as its final step (`scripts/smoke-test.sh`) and
**fails the rebuild** if the funnel doesn't serve the expected 4 tools. It retries
for ~45s because Funnel can take a few seconds to come live after `tailscale up`.
Run it anytime:

```bash
sudo bash scripts/smoke-test.sh
# discovers the funnel URL from `tailscale funnel status`, calls tools/list,
# asserts EXPECTED_TOOLS (default 4): ask_grok, get_odds, grok_x_search, ask_gemini
# tunables: EXPECTED_TOOLS, MCP_PATH (/mcp|/mcp/PATH), RETRIES, SLEEP_SECS, FUNNEL_URL
```

Or by hand:

```bash
BASE="$(tailscale funnel status | grep -oE 'https://[^ ]+' | head -n1)"
curl -s "$BASE/mcp" -X POST \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# expect 4 tools: ask_grok, get_odds, grok_x_search, ask_gemini
```

## Gotchas that cost hours

- **nvm node is not on `sudo`'s PATH.** systemd/sudo must use the absolute path
  `/root/.nvm/versions/node/v22.22.3/bin/node` (the unit already does).
- **`tsc` does not copy `kalshi-series.json`.** After every build, `cp
  src/kalshi-series.json build/` or `get_odds` breaks (setup.sh does this).
- **Gemini key must be RESTRICTED to the Generative Language API** or every
  `ask_gemini` call 403s.
- **Tailscale cert 500 after toggling HTTPS/DNS in the admin console:** run
  `sudo systemctl restart tailscaled` to force a netmap refresh, then retry.
- **Connector tool cache (Grok):** Grok caches the tool list per URL. If you add
  tools, point Grok at a new path (`/mcp/PATH`, …) to force a refresh.

## What's NOT in this repo (by design)

- `/etc/grok-mcp.env` — secrets.
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
scripts/commit-if-changed.sh          # commit repo iff dirty (hook + timer use it)
scripts/push-if-ahead.sh              # push to origin iff local is ahead (nightly timer only; off-box backup floor 24h)
scripts/warn-uncommitted.sh           # ~/.bashrc interactive reminder: warn if grok-mcp has uncommitted changes
scripts/smoke-test.sh                 # curl funnel + assert tool count; setup.sh's final self-check (retries while Funnel warms up)
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
