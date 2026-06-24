#!/usr/bin/env bash
# =============================================================================
# astra-config :: reproducible rebuild for host zaz-astra
# Goal: clone + run setup.sh + paste 2 API keys = working box in ~30 min.
# Idempotent: safe to re-run. Run as root from the repo root: sudo bash setup.sh
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP=/root/grok-mcp
APP_REPO="https://github.com/zazesty/ad-astra.git"
NODE_VERSION="22.22.3"
NVM_DIR=/root/.nvm
FUNNEL_PORT=3000

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo bash setup.sh)"; exit 1; }

# -----------------------------------------------------------------------------
say "1/10  APT packages (curl, git, tailscale)"
apt-get update -qq
apt-get install -y curl git
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg \
    | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
  curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list \
    | tee /etc/apt/sources.list.d/tailscale.list >/dev/null
  apt-get update -qq
  apt-get install -y tailscale
  systemctl enable --now tailscaled
fi

# -----------------------------------------------------------------------------
say "2/10  Node ${NODE_VERSION} via nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
fi
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"
nvm install "$NODE_VERSION"
nvm use "$NODE_VERSION"
NODE_BIN="$NVM_DIR/versions/node/v${NODE_VERSION}/bin"

# -----------------------------------------------------------------------------
say "3/10  Clone + build the MCP app (ad-astra -> $APP)"
if [ ! -d "$APP/.git" ]; then
  git clone "$APP_REPO" "$APP" || {
    echo "ERROR: clone of $APP_REPO failed. If that repo is PRIVATE, configure a" >&2
    echo "       token/SSH key for read access first, then re-run setup.sh." >&2
    exit 1
  }
fi
cd "$APP"
"$NODE_BIN/npm" ci
"$NODE_BIN/npm" run build
cp src/kalshi-series.json build/   # GOTCHA: tsc does not copy JSON; oddsTool needs it in build/
cd "$REPO"

# -----------------------------------------------------------------------------
say "4/10  Swap (2G /swapfile, swappiness via sysctl symlink later)"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

# -----------------------------------------------------------------------------
say "5/10  Symlink authored config (Stow-style: system -> repo)"
# systemd reads symlinked unit files fine after daemon-reload. Secrets/state are
# NEVER symlinked.
ln -sfnT "$REPO/etc/systemd/system/grok-mcp.service" /etc/systemd/system/grok-mcp.service
ln -sfnT "$REPO/etc/sysctl.d/99-swap.conf"            /etc/sysctl.d/99-swap.conf
chmod 644 "$REPO/etc/systemd/system/grok-mcp.service" "$REPO/etc/sysctl.d/99-swap.conf"
sysctl --system >/dev/null

mkdir -p /root/.config/systemd/user
ln -sfnT "$REPO/home/.config/systemd/user/astra-commit.service" /root/.config/systemd/user/astra-commit.service
ln -sfnT "$REPO/home/.config/systemd/user/astra-commit.timer"   /root/.config/systemd/user/astra-commit.timer
ln -sfnT "$REPO/home/.config/systemd/user/grok-model-check.service" /root/.config/systemd/user/grok-model-check.service
ln -sfnT "$REPO/home/.config/systemd/user/grok-model-check.timer"   /root/.config/systemd/user/grok-model-check.timer
ln -sfnT "$REPO/home/.config/systemd/user/gemini-model-check.service" /root/.config/systemd/user/gemini-model-check.service
ln -sfnT "$REPO/home/.config/systemd/user/gemini-model-check.timer"   /root/.config/systemd/user/gemini-model-check.timer
ln -sfnT "$REPO/home/.config/systemd/user/health-check.service" /root/.config/systemd/user/health-check.service
ln -sfnT "$REPO/home/.config/systemd/user/health-check.timer"   /root/.config/systemd/user/health-check.timer
ln -sfnT "$REPO/home/.config/systemd/user/drift-check.service" /root/.config/systemd/user/drift-check.service
ln -sfnT "$REPO/home/.config/systemd/user/drift-check.timer"   /root/.config/systemd/user/drift-check.timer

# Claude Code settings (permissions + SessionStart auto-commit hook)
mkdir -p /root/.claude
ln -sfnT "$REPO/home/.claude/settings.json" /root/.claude/settings.json

# ~/.bashrc is tracked in the repo (carries the interactive grok-mcp warn snippet +
# nvm setup). Symlink it into place; replaces the fresh-box default .bashrc.
ln -sfnT "$REPO/home/.bashrc" /root/.bashrc

# Belt-and-suspenders for the login warn net: if something atomic-replaces
# ~/.bashrc back into a plain file (nvm/rustup/editors do this, swapping the
# symlink away), re-append the interactive warn snippet — but ONLY if the marker
# is absent, so re-runs stay idempotent. Guarded with `case $- in *i*` so it
# never runs in non-interactive shells.
if ! grep -q 'warn-uncommitted.sh' /root/.bashrc 2>/dev/null; then
  cat >> /root/.bashrc <<'BASHRC_WARN'

# grok-mcp uncommitted-work reminder (interactive shells only)
case $- in
  *i*) /root/astra-config/scripts/warn-uncommitted.sh ;;
esac
BASHRC_WARN
fi

# Journaling usage-gated trigger: scripts symlinked Stow-style (canonical copies
# live in the repo). Secrets are scaffolded in step 7, crontab installed in step 8.
mkdir -p /root/journal-trigger
ln -sfnT "$REPO/home/journal-trigger/usage-gate.sh"      /root/journal-trigger/usage-gate.sh
ln -sfnT "$REPO/home/journal-trigger/journal-trigger.sh" /root/journal-trigger/journal-trigger.sh

# -----------------------------------------------------------------------------
say "6/10  Git hooks + script perms + push credential helper"
# Identity for commits made by the auto-commit hook/timer + manual commits.
git config --global user.name "Zavdi"
git config --global user.email "zazesty@gmail.com"
# Root-owned repo can trip Git's "dubious ownership" guard in some contexts;
# mark it safe so the hooks/timer never refuse to operate on it.
git config --global --add safe.directory "$REPO"
git -C "$REPO" config core.hooksPath .githooks
chmod +x "$REPO"/.githooks/* "$REPO"/scripts/*.sh "$REPO"/home/journal-trigger/*.sh

# Off-box auto-push (nightly, via astra-commit.service -> push-if-ahead.sh) needs
# a credential it can use unattended. credential.helper=store reads a plaintext
# token from ~/.git-credentials (root-owned, chmod 600). The TOKEN ITSELF is a
# secret and is NEVER in this repo — the operator supplies it once (below).
git config --global credential.helper store
# Non-interactive auth probe: GIT_TERMINAL_PROMPT=0 makes a missing credential
# fail fast instead of hanging setup waiting for a password.
if GIT_TERMINAL_PROMPT=0 git -C "$REPO" push --dry-run origin HEAD >/dev/null 2>&1; then
  echo "  push auth OK — nightly off-box backup is wired."
else
  echo "  ⚠️  No working push credential yet for $(git -C "$REPO" remote get-url origin)."
  echo "      The nightly 3am auto-push needs one. After setup, store it once:"
  echo "        printf 'https://<user>:<TOKEN>@github.com\\n' >> ~/.git-credentials"
  echo "        chmod 600 ~/.git-credentials   # then: git -C $REPO push"
  echo "      (Until then, commits are local-only; the login warn net will flag failures.)"
fi

# -----------------------------------------------------------------------------
say "7/10  Secrets file scaffold (blank; you fill it below)"
if [ ! -f /etc/grok-mcp.env ]; then
  cp "$REPO/.env.example" /etc/grok-mcp.env
  chmod 600 /etc/grok-mcp.env
fi

# Journaling /fire trigger creds — separate from the MCP env, and OUTSIDE the repo
# (never committed). Blank scaffold at mode 600; you paste the real values in
# step 10. Until then the trigger logs missing_secret_or_endpoint and fires nothing.
install -d -m 700 /root/.config/journal-trigger
install -d -m 755 /root/.local/state
[ -f /root/.config/journal-trigger/secret ]   || install -m 600 /dev/null /root/.config/journal-trigger/secret
[ -f /root/.config/journal-trigger/endpoint ] || install -m 600 /dev/null /root/.config/journal-trigger/endpoint

# -----------------------------------------------------------------------------
say "8/10  Enable units (system + user nightly commit timer)"
systemctl daemon-reload
systemctl enable grok-mcp.service
loginctl enable-linger root            # so the user timer runs without a login session
# Ensure the root user-systemd manager is up before --user calls (fresh box has none yet)
XDG_RUNTIME_DIR="/run/user/$(id -u)"; export XDG_RUNTIME_DIR
systemctl start "user@$(id -u).service" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now astra-commit.timer
systemctl --user enable --now grok-model-check.timer
systemctl --user enable --now gemini-model-check.timer
systemctl --user enable --now health-check.timer
systemctl --user enable --now drift-check.timer

# Journaling scheduler: install the hourly 1-6am PT root crontab. This is the
# only root crontab on this box, so a plain install is correct on a fresh rebuild.
# (Edits to crontab.txt require re-running this line — a symlink can't drive the
# cron spool. The scripts it calls ARE symlinks, so script edits need no re-install.)
crontab "$REPO/home/journal-trigger/crontab.txt"

# -----------------------------------------------------------------------------
say "9/10  Tailscale auth + Funnel"
# Interactive: opens a browser-auth URL. Re-auth this box to the tailnet.
tailscale up
# Public HTTPS -> local MCP server. Persists in tailscaled state, survives reboot.
tailscale funnel --bg "$FUNNEL_PORT"
# Toggling HTTPS/Funnel/DNS can leave tailscaled serving a stale cert (intermittent
# 500 on the funnel URL). Restarting clears it; auth + the --bg funnel config both
# persist in tailscaled state across the restart, so this is safe to re-run.
systemctl restart tailscaled

# -----------------------------------------------------------------------------
say "10/10  PAUSE — paste the two API keys"
cat <<EOF

  Edit /etc/grok-mcp.env and fill in the keys (it was scaffolded blank):
      XAI_API_KEY=xai-...        (https://console.x.ai      — set a spend cap)
      GEMINI_API_KEY=...         (https://aistudio.google.com — RESTRICT key to
                                  the Generative Language API, or it's blocked)
      OPENROUTER_API_KEY=sk-or-... (https://openrouter.ai/keys — REQUIRED for the
                                  default GEMINI_TRANSPORT=openrouter; also add the
                                  GEMINI_API_KEY as a BYOK provider key in the
                                  OpenRouter dashboard. To run with just the first
                                  two keys instead, set GEMINI_TRANSPORT=direct.)

  OPTIONAL (alert emails; absent = alerts safely no-op):
      RESEND_API_KEY=...         (https://resend.com — sign up with the address
                                  in NOTIFY_EMAIL_TO so no-domain self-send works)
      NOTIFY_EMAIL_TO=zazesty@gmail.com

  In another shell:  sudo nano /etc/grok-mcp.env

  OPTIONAL (journaling auto-trigger; absent = the cron runs but fires nothing):
      In Claude Code on the web, create/locate the Routine for zazesty/Journaling,
      add an API trigger, and copy its URL + generate its token (shown ONCE). Then,
      pasting each value alone (Enter, then Ctrl-D):
        cat > /root/.config/journal-trigger/endpoint   # the /fire URL
        cat > /root/.config/journal-trigger/secret      # the sk-ant-oat01-... token
      (Both files were scaffolded blank at mode 600 in step 7. No restart needed —
       the next cron tick picks them up.)

EOF
read -rp "Press Enter once BOTH keys are saved in /etc/grok-mcp.env... " _

systemctl start grok-mcp.service || systemctl restart grok-mcp.service
sleep 2
say "Service status:"
systemctl --no-pager --lines=0 status grok-mcp.service || true

# -----------------------------------------------------------------------------
say "Smoke-test — curl the funnel + assert tool count (retries while Funnel warms up)"
# Self-check: hits the PUBLIC funnel URL and asserts EXPECTED_TOOLS tools are
# served. Retries internally because Funnel can take a few seconds to come live
# after a fresh `tailscale up`. Loud + fatal on failure so a broken rebuild
# can't pass silently.
if bash "$REPO/scripts/smoke-test.sh"; then
  say "Done. Rebuild verified end-to-end. ✅"
  cat <<'EOF'

  ↻ Server side is green — but consumers pointing at the OLD MCP_PATH won't
    follow automatically. If the path changed (rebuild or rotation), repoint
    each by hand — these fail SILENTLY when stale:
      • Claude Code journaling routine — connector URL (cloud routine config)
      • Claude interactive connector (astra85f) — reconnect to the new URL
      • Grok connector — reconnect (also busts its per-URL tool cache)
    Full checklist: README "Rotating the URL (MCP_PATH)".
EOF
else
  echo
  echo "⚠️  Smoke-test FAILED — the box is built but the funnel isn't serving the"
  echo "    expected tools yet. Check: systemctl status grok-mcp.service and"
  echo "    tailscale funnel status, then re-run: sudo bash scripts/smoke-test.sh"
  exit 1
fi
