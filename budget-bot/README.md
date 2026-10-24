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

## Tax book

Separate from pace. Characters live in `~/.local/state/budget-bot/accounting/tax-classifications.json` (never git). A locked row keeps its coach category across Plaid upsert, and auto-review will not retag it. `counts_as_spend` follows the ruling.

```bash
python3 -m budget_bot tax-tie-out --year 2026
python3 -m budget_bot tax-quarterly --dry-run --as-of 2027-01-03
```

The quarterly mail is email only, first 7 days of Jan/Apr/Jul/Oct, 09:00 PT, for the quarter that just closed. It is not a Pushover. There is no asset register.
