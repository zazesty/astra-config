#!/usr/bin/env bash
set -euo pipefail

# Locate the real codex binary, skipping this wrapper and agent-guard
SELF_REAL="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || printf '%s' "${BASH_SOURCE[0]}")"
GUARD_REAL="$(readlink -f /root/astra-config/scripts/agent-guard.sh 2>/dev/null || true)"
REAL_CODEX=""
while IFS= read -r cand; do
  [ -n "$cand" ] || continue
  cand_real="$(readlink -f "$cand" 2>/dev/null || true)"
  if [ "$cand_real" != "$SELF_REAL" ] && [ "$cand_real" != "$GUARD_REAL" ]; then
    REAL_CODEX="$cand"
    break
  fi
done < <(type -aP codex 2>/dev/null || true)

if [[ -z "$REAL_CODEX" && -x /root/.nvm/versions/node/v22.22.3/bin/codex ]]; then
  REAL_CODEX="/root/.nvm/versions/node/v22.22.3/bin/codex"
fi
if [[ -z "$REAL_CODEX" ]]; then
  echo "codex wrapper: could not locate real codex binary" >&2
  exit 1
fi

# 1. No args (interactive TUI) or piped stdin without args
if [[ $# -eq 0 ]]; then
  if [[ -t 0 ]]; then
    exec "$REAL_CODEX"
  else
    err_file="$(mktemp)"
    trap 'rm -f "$err_file"' EXIT
    if ! "$REAL_CODEX" exec - 2>"$err_file"; then
      cat "$err_file" >&2
      exit 1
    fi
    exit 0
  fi
fi

# 2. Pass through native subcommands and info flags untouched
case "$1" in
  exec|e|resume|fork|review|login|logout|mcp|features|agents|queue|archive|delete|unarchive|app-server|exec-server|-h|--help|-V|--version)
    exec "$REAL_CODEX" "$@"
    ;;
esac

# 3. Quiet follow-up shorthand: `codex -c "follow-up prompt"` resumes last exec session silently
mode=("exec")
if [[ "$1" == "-c" || "$1" == "--continue" ]]; then
  shift
  mode=("exec" "resume" "--last")
fi

# 4. Run non-interactively with stderr suppressed unless the command fails
err_file="$(mktemp)"
trap 'rm -f "$err_file"' EXIT

if ! "$REAL_CODEX" "${mode[@]}" "$@" 2>"$err_file"; then
  cat "$err_file" >&2
  exit 1
fi
