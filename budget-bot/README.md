# Budget Bot

Finance coach on **zaz-astra**: hardcap, pace, statement SSOT, Plaid live fill-in, canned status.

- **Product name:** Budget Bot
- **Live path (legacy slug):** `/root/hermes-finance` → symlink to this tree (`astra-config/budget-bot`)
- **Package / CLI:** `python3 -m hermes_finance` (stable until an explicit rename)
- **State (never git):** `~/.local/state/hermes-finance/` and `/etc/hermes-finance.env`

This directory is **code + synthetic fixtures only**. No live txns, statements, or tokens.

## Quick start

```bash
cd /root/hermes-finance   # or this directory
python3 -m unittest discover -s tests -q
python3 -m hermes_finance budget-status
```

`setup.sh` links this tree to `/root/hermes-finance` and does not copy bank data.
