# Agent Guidelines (dev user)

- Run agents inside tmux (`tmux new -s work` or `tmux attach -t work`).
- Write state to `NOTES.md` before reboots; resume with `claude --continue` after.
- **No sudo for file edits:** Project directories are owned by `dev:dev`. Do not use `sudo` for editing code or project files.
