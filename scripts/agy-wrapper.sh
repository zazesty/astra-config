#!/usr/bin/env bash
# agy-wrapper.sh — ensure ~/.config/agy-vertex.env is loaded before launching agy
# so pre-existing or non-login shells do not fall back to consumer OAuth, and
# default interactive/prompt launches to --dangerously-skip-permissions.
set -euo pipefail
if [ -f "${HOME:-}/.config/agy-vertex.env" ]; then
    # shellcheck disable=SC1090
    . "${HOME}/.config/agy-vertex.env"
fi

inject_yolo=1
if [ $# -gt 0 ]; then
    case "$1" in
        acp|agent|agents|changelog|help|install|mcp|mic-serve|models|plugin|plugins|remote-control|update|-h|--help|-V|--version)
            inject_yolo=0
            ;;
    esac
fi
for arg in "$@"; do
    case "$arg" in
        --dangerously-skip-permissions|--mode|--mode=*|-h|--help|-V|--version)
            inject_yolo=0
            break
            ;;
    esac
done

if [ "$inject_yolo" -eq 1 ]; then
    exec /root/.local/bin/agy --dangerously-skip-permissions "$@"
fi
exec /root/.local/bin/agy "$@"
