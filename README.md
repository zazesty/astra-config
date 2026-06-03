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

- Public: `https://zaz-astra.tail5d74e1.ts.net/mcp` (and `/mcp/v2` alias).
- Local: `127.0.0.1:3000`. Funnel terminates TLS; the node server is plain HTTP on loopback.

## Verify

```bash
curl -s https://zaz-astra.tail5d74e1.ts.net/mcp -X POST \
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
- **Authless server** — the Funnel URL is public. The xAI + Google **spend caps
  are the entire safety net.** Set them.
- **Connector tool cache (Grok):** Grok caches the tool list per URL. If you add
  tools, point Grok at a new path (`/mcp/v3`, …) to force a refresh.

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
home/.claude/settings.json            # Claude Code SessionStart hooks (symlinked into place)
scripts/commit-if-changed.sh          # commit repo iff dirty (hook + timer use it)
scripts/warn-uncommitted.sh           # SessionStart: warn if grok-mcp has uncommitted changes
.githooks/pre-commit                  # aborts commits containing key-shaped strings
.env.example                          # template for /etc/grok-mcp.env
setup.sh                              # idempotent rebuild
```

## Automation (keeps this repo current)

- **SessionStart Claude hooks** (`~/.claude/settings.json`, itself symlinked into this
  repo so it's captured): (1) `commit-if-changed.sh` auto-commits this repo; (2)
  `warn-uncommitted.sh` warns at session start if **grok-mcp** (the app repo) has
  uncommitted changes — it is NOT auto-committed; commit+push it via its own flow.
- **Nightly user timer** (`astra-commit.timer`, 03:00) as the floor.
- Both auto-commits are **commit only** (local). Push deliberately with your token:
  `git -C /root/astra-config push`.
