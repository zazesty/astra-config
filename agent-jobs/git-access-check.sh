#!/usr/bin/env bash
# git-access-check — irritation #2 fix.
# Probes GitHub API: can the box PAT see each expected repo, and does it have push?
# Fine-grained PATs 404 brand-new private repos until allowlisted — this emails
# instead of failing silently on the next git push.
#
# Config (optional): ~/.config/agent-jobs/git-repos.list  one "owner/repo" per line
# Default list below if missing.
set -euo pipefail

STATE_DIR="${HOME}/.local/state/agent-jobs"
mkdir -p "$STATE_DIR"
REPORT="$STATE_DIR/git-access-check.report.json"
LIST_FILE="${HOME}/.config/agent-jobs/git-repos.list"
NOTIFY="${NOTIFY_CMD:-/root/astra-config/scripts/notify-email.sh}"
CRED="${HOME}/.git-credentials"

DEFAULT_REPOS=(
  "zazesty/astra-config"
  "zazesty/ad-astra"
  "zazesty/Journaling"
  "zazesty/Grok-Journal"
)

if [ ! -r "$CRED" ]; then
  echo "git-access-check: missing $CRED" >&2
  exit 1
fi

export CRED REPORT LIST_FILE
export DEFAULT_REPOS_CSV
DEFAULT_REPOS_CSV=$(IFS=,; echo "${DEFAULT_REPOS[*]}")

python3 <<'PY'
import json, os, urllib.request, urllib.error
from pathlib import Path

cred = Path(os.environ["CRED"]).read_text().strip().splitlines()[0]
rest = cred.split("://", 1)[1]
_user, rest2 = rest.split(":", 1)
token, _host = rest2.rsplit("@", 1)

list_file = Path(os.environ["LIST_FILE"])
if list_file.is_file():
    repos = [ln.strip() for ln in list_file.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
else:
    repos = [r for r in os.environ["DEFAULT_REPOS_CSV"].split(",") if r]

results = []
bad = []
for full in repos:
    if "/" not in full:
        results.append({"repo": full, "ok": False, "error": "bad_name"})
        bad.append(full)
        continue
    owner, name = full.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{name}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "zaz-astra-git-access-check",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
            perms = d.get("permissions") or {}
            push = bool(perms.get("push"))
            row = {
                "repo": full,
                "ok": push,
                "private": d.get("private"),
                "permissions": perms,
                "http": r.status,
            }
            if not push:
                bad.append(full)
            results.append(row)
    except urllib.error.HTTPError as e:
        results.append({"repo": full, "ok": False, "http": e.code, "error": str(e.reason)})
        bad.append(full)
    except Exception as e:
        results.append({"repo": full, "ok": False, "error": type(e).__name__})
        bad.append(full)

doc = {
    "schema": "git-access-check/v1",
    "ok": len(bad) == 0,
    "bad": bad,
    "results": results,
}
Path(os.environ["REPORT"]).write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps({"ok": doc["ok"], "bad": bad, "n": len(results)}))
if bad:
    raise SystemExit(1)
PY
RC=$?

if [ "$RC" -ne 0 ] && [ -f "$NOTIFY" ]; then
  {
    echo "Box GitHub PAT cannot push (or even see) one or more expected repos."
    echo "Fine-grained tokens often 404 new private repos until allowlisted."
    echo
    echo "Report: $REPORT"
    echo
    cat "$REPORT"
    echo
    echo "Fix: GitHub → Settings → Developer settings → Fine-grained token"
    echo "  → Repository access → add the missing private repo(s) with Contents: Read/Write."
    echo "Or update ~/.config/agent-jobs/git-repos.list if the list is wrong."
  } | bash "$NOTIFY" "🔴 git PAT missing repo access" || true
fi
exit "$RC"
