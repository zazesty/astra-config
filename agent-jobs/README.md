# agent-jobs

Thin scheduled-agent runner for zaz-astra (**irritation #4 fix**: one convention).

## Convention

| Piece | Path |
|-------|------|
| Runner | `scripts/agent-run.sh <job-id> [--force]` |
| Job body | `agent-jobs/<job-id>.sh` (executable) |
| Disable | `~/.config/agent-jobs/<job-id>.disabled` |
| Lock / log / last | `~/.local/state/agent-jobs/` |
| Timer | oneshot user service → `agent-run.sh <id>` |

Optional: `AGENT_TIMEOUT_SECS` (default 300), `AGENT_EMAIL_ON_FAIL=1` for generic fail mail.

## Jobs

| Job | Timer | Purpose |
|-----|-------|---------|
| `ops-log` | 6h | Machine JSON box snapshot |
| `git-access-check` | daily 12:30 PT | PAT can see+push expected repos |
| `consumer-health` | daily 12:45 PT | Smoke + Grok Build path + journal trigger/fire stale |
| `pause-reminder` | Sun 17:00 PT | Grok journal autopilot still paused? |

## New private GitHub repo checklist

1. Create private repo  
2. **Add it to the fine-grained PAT** allowlist used in `~/.git-credentials` (Contents R/W)  
3. Add `owner/repo` to `~/.config/agent-jobs/git-repos.list` if not in defaults  
4. `agent-run.sh git-access-check --force` should go green  
5. Clone under `/root/…` and prove `git push`
