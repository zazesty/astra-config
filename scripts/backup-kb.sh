#!/usr/bin/env bash
# backup-kb.sh — scrub memory + standing-todos and force-push to private GitHub.
#
# Default remote: private repo Grok-Journal, orphan branch kb-backup
# (PAT on box cannot create new repos; migrate to zazesty/astra-kb when scopes allow.)
#
# NEVER packs Hermes bank data.
set -euo pipefail

REPO_SLUG="${KB_BACKUP_REPO:-zazesty/Grok-Journal}"
BRANCH="${KB_BACKUP_BRANCH:-kb-backup}"
STAGING="${TMPDIR:-/tmp}/astra-kb-export-$$"
WORK="${TMPDIR:-/tmp}/astra-kb-push-$$"

cleanup() { rm -rf "$STAGING" "$WORK"; }
trap cleanup EXIT

mkdir -p "$STAGING/memory" "$STAGING/state"

python3 - <<'PY' "$STAGING"
import re, sys
from pathlib import Path

staging = Path(sys.argv[1])
src = Path("/root/memory")
dst = staging / "memory"
dst.mkdir(parents=True, exist_ok=True)

patterns = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"), "sk-REDACTED"),
    (re.compile(r"\bxai-[A-Za-z0-9_-]{10,}\b"), "xai-REDACTED"),
    (re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{10,}\b"), "sk-or-REDACTED"),
    (re.compile(r"\bsk-ant-oat01-[A-Za-z0-9_-]{10,}\b"), "sk-ant-REDACTED"),
    (re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*\S+"), r"\1=REDACTED"),
    (re.compile(r"/mcp/[a-f0-9]{12,}"), "/mcp/REDACTED"),
    (re.compile(r"https://[a-z0-9.-]+\.ts\.net/\S+"), "https://HOST.ts.net/REDACTED"),
]

redacted = 0
for p in sorted(src.iterdir()):
    if p.name.startswith(".") or p.suffix not in {".md", ".json"}:
        if p.name not in {"MEMORY.md", "index.md"} and p.suffix != ".md":
            continue
    if not p.is_file():
        continue
    text = p.read_text(errors="replace")
    orig = text
    for pat, rep in patterns:
        text = pat.sub(rep, text)
    if text != orig:
        redacted += 1
    (dst / p.name).write_text(text)

todos = Path.home() / ".local/state/astra/standing-todos.json"
if todos.is_file():
    t = todos.read_text()
    for pat, rep in patterns:
        t = pat.sub(rep, t)
    (staging / "state" / "standing-todos.json").write_text(t)

blob = "\n".join(x.read_text(errors="ignore") for x in dst.glob("*.md"))
for bad in [r"sk-[A-Za-z0-9]{20,}", r"xai-[A-Za-z0-9]{20,}", r"sk-or-v1-[A-Za-z0-9]{20,}"]:
    if re.search(bad, blob):
        raise SystemExit(f"scrub failed residual {bad}")
print(f"scrub_ok redacted_files={redacted}")
PY

cat > "$STAGING/README.md" <<EOF
# astra-kb backup (\`$BRANCH\` on $REPO_SLUG)

Private scrubbed backup of zaz-astra memory + standing todos. No Hermes bank data.
EOF

TOKEN=$(python3 -c "from pathlib import Path; import urllib.parse; u=urllib.parse.urlparse(Path.home().joinpath('.git-credentials').read_text().strip().splitlines()[0]); print(u.password)")
mkdir -p "$WORK"
git clone --depth 1 "https://zazesty:${TOKEN}@github.com/${REPO_SLUG}.git" "$WORK/repo" >/dev/null 2>&1
cd "$WORK/repo"
git checkout --orphan "$BRANCH"
git rm -rf . >/dev/null 2>&1 || true
cp -a "$STAGING"/* .
git config user.email "zazesty@users.noreply.github.com"
git config user.name "zaz-astra-backup"
git add -A
git commit -q -m "backup: scrubbed memory + todos $(date -u +%Y-%m-%dT%H%MZ)"
git push -f origin "$BRANCH" 2>&1 | sed -E 's|//[^@]+@|//REDACTED@|g'
unset TOKEN
echo "ok: https://github.com/${REPO_SLUG}/tree/${BRANCH}"
