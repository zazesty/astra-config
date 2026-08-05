# Health Bot — public program site

- Index: https://zaz-astra.tail5d74e1.ts.net/health-bot/
- Privacy: https://zaz-astra.tail5d74e1.ts.net/health-bot/privacy.html

Public by design for Kaiser / Patient Access app registration. Do **not** put MCP mount paths or secrets here.

## OAuth redirect URIs (Kaiser Secure API / Patient Access)

Register these **exactly** (HTTPS). Prefer the `.html` form — query strings (`?code=`) work reliably:

| Env | Redirect URI |
|-----|----------------|
| **Sandbox** | `https://zaz-astra.tail5d74e1.ts.net/health-bot/oauth/sandbox/callback.html` |
| **Production** | `https://zaz-astra.tail5d74e1.ts.net/health-bot/oauth/callback.html` |

Directory variants (trailing slash required on Funnel static serve):

- `…/health-bot/oauth/sandbox/callback/`
- `…/health-bot/oauth/callback/`

Callback pages show `code` / `state` / errors after redirect. Token exchange stays on-box (`/etc/health-bot.env`, state under `~/.local/state/health/`). Never commit auth codes.

## Funnel

```bash
tailscale funnel --bg --yes --set-path=/health-bot /root/astra-config/public/health-bot
```
