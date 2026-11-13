# Antigravity Root-Sector Handoff (2026-11-13)

## 1. Auth & Model Alignment (Action Needed as `root`)
The `dev` session could not update `/root/.gemini/antigravity-cli/` because those files are `0600 root:root`. From `root`:

1. **Model:** Set `/root/.gemini/antigravity-cli/settings.json` to match `dev`:
   - `"model": "Gemini 4 Hydrogen (Medium)"`
   - `"modelProvider": "gemini"`
2. **Billing / API key:** Align `/root/.gemini/antigravity-cli/` auth to use the same Gemini API key as `dev` (the `GEMINI_API_KEY` from `/home/dev/.agent-env` or `/etc/grok-mcp.env` instead of OAuth/subscription billing).

## 2. Context & Pricing Notes
- **Gemini 4 Hydrogen (`gemini-4-hydrogen`):** $1.50 / 1M input, $7.50 / 1M output (thinking tokens billed as output). Supports `low`, `medium`, `high` (default), and `xhigh`.
- **Thinking on Medium:** Expect thinking tokens on Medium (unlike Argon Medium, which emitted 0 in the 2026-10-13 bakeoff; 3.8 Flash Medium emitted a median 817 thinking tokens per answer).
- **Local thinking-token logging:** Antigravity's built-in DBs drop thinking tokens. To record them locally, either:
  1. Extend `/root/astra-config/scripts/argon-thought-proxy.py` (`127.0.0.1:8791`) to append `{ts, model, input, cached, output, thoughts}` per response to a JSONL file and point Antigravity's Gemini base URL at it, or
  2. Route the key through OpenRouter BYOK so `/root/astra-config/scripts/ai-spend-review.py` captures exact billed USD + tokens automatically.

## 3. Already Completed on `root`
- **Startup guard (`agent-guard.sh`):** `/root/.bashrc` (`/root/astra-config/home/.bashrc`) now wraps interactive `grok`, `codex`, and `claude` calls to `/usr/local/bin/{grok,codex,claude}` (which point to `/root/astra-config/scripts/agent-guard.sh`), overriding the `PATH` precedence of `nvm` and `~/.grok/bin`.
