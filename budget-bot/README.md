# Budget Bot

Finance coach on **zaz-astra**: hardcap, pace, statement SSOT, Plaid live fill-in, canned status.

- **Product name:** Budget Bot
- **Live path:** `/root/budget-bot` → this tree (`astra-config/budget-bot`). `/root/hermes-finance` is a compatibility symlink.
- **Package / CLI:** `python3 -m budget_bot` (`python3 -m hermes_finance` is a shim)
- **State (never git):** `~/.local/state/budget-bot/` and `/etc/budget-bot.env` (old `hermes-finance` paths are aliases)

This directory is **code + synthetic fixtures only**. No live txns, statements, or tokens.

## Quick start

```bash
cd /root/budget-bot   # or this directory
python3 -m unittest discover -s tests -q
python3 -m budget_bot budget-status
```

`setup.sh` links this tree to `/root/budget-bot` and does not copy bank data.
