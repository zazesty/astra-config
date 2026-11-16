#!/usr/bin/env bash
# agy-wrapper.sh — launch agy with --add-dir / without --dangerously-skip-permissions
# so tool approvals rely on settings.json ("toolPermission": "always-proceed").
#
# Invocation modes:
#   agy / antigravity : on root, defaults to AI Pro consumer OAuth (unsets Vertex ADC env);
#                       on non-root (e.g. dev), loads ~/.config/agy-vertex.env if present.
#   agy-gcp           : loads ~/.config/agy-vertex.env (Vertex AI / GCP ADC) for GCP credit backup.
set -euo pipefail

CMD_NAME="$(basename "$0")"
if [ "$CMD_NAME" = "agy-gcp" ]; then
    if [ -f "${HOME:-}/.config/agy-vertex.env" ]; then
        # shellcheck disable=SC1090
        . "${HOME}/.config/agy-vertex.env"
    else
        export AGY_ADC_AUTH=true
        export GOOGLE_CLOUD_PROJECT=gen-lang-client-0789043233
        export GOOGLE_CLOUD_QUOTA_PROJECT=gen-lang-client-0789043233
        export GOOGLE_CLOUD_LOCATION=global
    fi
elif [ "$(id -u)" -eq 0 ]; then
    unset AGY_ADC_AUTH GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_QUOTA_PROJECT GOOGLE_CLOUD_LOCATION
elif [ -f "${HOME:-}/.config/agy-vertex.env" ]; then
    # shellcheck disable=SC1090
    . "${HOME}/.config/agy-vertex.env"
fi

inject_root=1
if [ $# -gt 0 ]; then
    case "$1" in
        acp|agent|agents|changelog|help|install|mcp|mic-serve|models|plugin|plugins|remote-control|update|-h|--help|-V|--version)
            inject_root=0
            ;;
    esac
fi

prev=""
for arg in "$@"; do
    case "$arg" in
        --add-dir=/|-h|--help|-V|--version)
            inject_root=0
            break
            ;;
        /)
            if [ "$prev" = "--add-dir" ]; then
                inject_root=0
                break
            fi
            ;;
    esac
    prev="$arg"
done

[ "${PWD:-}" = "/" ] && inject_root=0

if [ "$inject_root" -eq 1 ]; then
    exec /root/.local/bin/agy --add-dir / "$@"
fi
exec /root/.local/bin/agy "$@"
