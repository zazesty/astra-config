#!/usr/bin/env bash
# agent-guard.sh — startup friction & manual confirmation for coding agents.
# Intercepts interactive launches of grok, claude, codex, agy.
#
# Bypass options:
#   CLI flag:       --yes or -y (stripped before calling underlying binary)
#   Environment:    AGENT_FORCE=1 or AGENT_CONFIRMED=1
#   Non-interactive: [ ! -t 0 ] || [ ! -t 1 ] automatically bypasses
set -euo pipefail

CMD_NAME="$(basename "$0")"

if [ "$CMD_NAME" = "agent-guard.sh" ] || [ "$CMD_NAME" = "agent-guard" ]; then
    if [ $# -gt 0 ]; then
        CMD_NAME="$1"
        shift
    else
        echo "Usage: agent-guard <grok|claude|codex|agy> [args...]" >&2
        exit 2
    fi
fi

# Locate the real binary for the invoked command
REAL_BIN=""
case "$CMD_NAME" in
    grok)
        if [ -x "${HOME:-}/.grok/bin/grok" ] && [ "$(readlink -f "${HOME:-}/.grok/bin/grok")" != "$(readlink -f "$0")" ]; then
            REAL_BIN="${HOME:-}/.grok/bin/grok"
        elif [ -x /root/.grok/bin/grok ] && [ "$(readlink -f /root/.grok/bin/grok)" != "$(readlink -f "$0")" ]; then
            REAL_BIN="/root/.grok/bin/grok"
        elif [ -x /root/.grok/downloads/grok-linux-x86_64 ]; then
            REAL_BIN="/root/.grok/downloads/grok-linux-x86_64"
        fi
        ;;
    claude)
        # Check ~/.local/share/claude/versions (latest)
        if [ -d "${HOME:-}/.local/share/claude/versions" ]; then
            REAL_BIN=$(ls -td "${HOME:-}/.local/share/claude/versions/"* 2>/dev/null | head -n 1 || true)
        fi
        if [ -z "$REAL_BIN" ] || [ ! -x "$REAL_BIN" ]; then
            if [ -d /root/.local/share/claude/versions ]; then
                REAL_BIN=$(ls -td /root/.local/share/claude/versions/* 2>/dev/null | head -n 1 || true)
            fi
        fi
        ;;
    codex)
        if command -v node >/dev/null 2>&1; then
            NODE_DIR="$(dirname "$(readlink -f "$(command -v node)")")"
            if [ -x "$NODE_DIR/codex" ] && [ "$(readlink -f "$NODE_DIR/codex")" != "$(readlink -f "$0")" ]; then
                REAL_BIN="$NODE_DIR/codex"
            fi
        fi
        if [ -z "$REAL_BIN" ] && [ -x /root/.nvm/versions/node/v22.22.3/bin/codex ]; then
            REAL_BIN="/root/.nvm/versions/node/v22.22.3/bin/codex"
        fi
        ;;
    agy|antigravity)
        if [ -x /root/.local/bin/agy ] && [ "$(readlink -f /root/.local/bin/agy)" != "$(readlink -f "$0")" ]; then
            REAL_BIN="/root/.local/bin/agy"
        fi
        ;;
    *)
        echo "agent-guard: unknown command '$CMD_NAME'" >&2
        exit 2
        ;;
esac

if [ -z "$REAL_BIN" ] || [ ! -x "$REAL_BIN" ]; then
    echo "agent-guard: error: could not locate real binary for '$CMD_NAME'" >&2
    exit 1
fi

# Determine whether to prompt or bypass
BYPASS=0

# Automatic bypass for non-interactive environments (cron, subagents, pipes)
if [ ! -t 0 ] || [ ! -t 1 ]; then
    BYPASS=1
fi

# Automatic bypass via environment variables
if [ "${AGENT_FORCE:-0}" = "1" ] || [ "${AGENT_CONFIRMED:-0}" = "1" ]; then
    BYPASS=1
fi

# Filter out --yes / -y from forwarded args, and bypass for info flags
PASSTHROUGH_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --yes|-y)
            BYPASS=1
            ;;
        --help|-h|--version|-v)
            BYPASS=1
            PASSTHROUGH_ARGS+=("$arg")
            ;;
        doctor|login|logout)
            BYPASS=1
            PASSTHROUGH_ARGS+=("$arg")
            ;;
        *)
            PASSTHROUGH_ARGS+=("$arg")
            ;;
    esac
done

if [ "$BYPASS" -eq 0 ]; then
    GIT_INFO="not a git repo"
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
        if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
            STATUS="dirty"
        else
            STATUS="clean"
        fi
        GIT_INFO="$BRANCH ($STATUS)"
    fi

    TMUX_INFO="no"
    if [ -n "${TMUX:-}" ]; then
        TMUX_INFO="active"
    fi

    echo "==> [agent-guard] Starting: $CMD_NAME"
    echo "    Directory:   $(pwd)"
    echo "    Git status:  $GIT_INFO"
    echo "    Tmux:        $TMUX_INFO"
    if [ "$TMUX_INFO" = "no" ]; then
        echo "    [note: CLAUDE.md recommends running agents inside tmux]"
    fi
    echo ""
    read -r -p "Start $CMD_NAME session? [y/N] (use --yes to bypass): " CONFIRM || true
    case "$CONFIRM" in
        y|Y|yes|YES)
            echo "==> [agent-guard] Proceeding..."
            ;;
        *)
            echo "==> [agent-guard] Aborted."
            exit 1
            ;;
    esac
fi

exec "$REAL_BIN" "${PASSTHROUGH_ARGS[@]}"
