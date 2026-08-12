#!/usr/bin/env bash
# =============================================================================
# grok-journal-read-reminder.sh — weekly nudge to read last week's Grok journal
#
# Saturday noon PT (timer). Email only: short reminder + GitHub links to
# entries whose metadata PT date falls in the last 7 calendar days (inclusive
# of today PT). No Pushover. Fail-open via notify-email.
#
# Usage:
#   grok-journal-read-reminder.sh           # send email
#   grok-journal-read-reminder.sh --dry-run # print body, no send
# =============================================================================
set -uo pipefail

DRY_RUN=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
  esac
done

REPO_ASTRA="${1:-/root/astra-config}"
# allow --dry-run as only arg
if [ "$REPO_ASTRA" = "--dry-run" ]; then
  REPO_ASTRA=/root/astra-config
fi
NOTIFY="$REPO_ASTRA/scripts/notify-email.sh"
JREPO="${GROK_JOURNAL_REPO:-/root/Grok-Journal}"
JDIR="$JREPO/Grok_Journal"
TZPT=America/Los_Angeles
GH_BASE="https://github.com/zazesty/Grok-Journal/blob/main"

if [ ! -d "$JDIR" ]; then
  echo "grok-journal-read-reminder: missing $JDIR" >&2
  exit 0
fi

# Pull so links match remote (best-effort; fail-open)
if [ -d "$JREPO/.git" ]; then
  git -C "$JREPO" pull --ff-only origin main >/dev/null 2>&1 || true
fi

MAP=$(JDIR="$JDIR" TZPT="$TZPT" GH_BASE="$GH_BASE" python3 - <<'PY'
import os, re, pathlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

jdir = pathlib.Path(os.environ["JDIR"])
tz = ZoneInfo(os.environ["TZPT"])
gh = os.environ["GH_BASE"].rstrip("/")
today = datetime.now(tz).date()
start = today - timedelta(days=6)  # last 7 calendar days inclusive

# *Entry 018 · 11 August 2026, 2:00 AM PT · model: Grok 4.5*
meta_re = re.compile(
    r"^\*Entry\s+(\d+)\s*·\s*(\d{1,2}\s+\w+\s+\d{4})",
    re.M,
)
# fallback: entry018-slug.md
name_re = re.compile(r"^entry(\d{3})-", re.I)

rows = []
for path in sorted(jdir.rglob("entry*.md")):
    if path.name == ".gitkeep":
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:800]
    except OSError:
        continue
    m = meta_re.search(text)
    entry_num = None
    d = None
    if m:
        entry_num = int(m.group(1))
        try:
            d = datetime.strptime(m.group(2).strip(), "%d %B %Y").date()
        except ValueError:
            try:
                d = datetime.strptime(m.group(2).strip(), "%d %b %Y").date()
            except ValueError:
                d = None
    if entry_num is None:
        nm = name_re.match(path.name)
        if nm:
            entry_num = int(nm.group(1))
    if d is None:
        # mtime in PT as last resort
        d = datetime.fromtimestamp(path.stat().st_mtime, tz).date()
    if d < start or d > today:
        continue
    repo_root = jdir.parent  # .../Grok-Journal
    try:
        repo_rel = path.relative_to(repo_root)
    except ValueError:
        repo_rel = pathlib.Path("Grok_Journal") / path.name
    url = f"{gh}/{repo_rel.as_posix()}"
    handle = ""
    hm = re.search(r'^#\s*[\"“](.+?)[\"”]', text, re.M)
    if hm:
        handle = hm.group(1).strip()
    rows.append((entry_num or 0, d, handle, url, path.name))

rows.sort(key=lambda r: (r[1], r[0]))
print(f"START={start.isoformat()}")
print(f"END={today.isoformat()}")
print(f"COUNT={len(rows)}")
for entry_num, d, handle, url, name in rows:
    label = f'Entry {entry_num:03d}' if entry_num else name
    if handle:
        label += f' — "{handle}"'
    label += f" ({d.strftime('%-d %b')})"
    print(f"ITEM|{label}|{url}")
PY
)

START=$(printf '%s\n' "$MAP" | sed -n 's/^START=//p' | head -1)
END=$(printf '%s\n' "$MAP" | sed -n 's/^END=//p' | head -1)
COUNT=$(printf '%s\n' "$MAP" | sed -n 's/^COUNT=//p' | head -1)
COUNT="${COUNT:-0}"

FOLDER_URL="https://github.com/zazesty/Grok-Journal/tree/main/Grok_Journal"

LINKS=""
while IFS= read -r line; do
  case "$line" in
    ITEM\|*)
      rest="${line#ITEM|}"
      label="${rest%%|*}"
      url="${rest#*|}"
      LINKS+="  • ${label}
    ${url}
"
      ;;
  esac
done <<< "$MAP"

if [ "$COUNT" = "0" ]; then
  LINKS="  (no entries with PT dates in this window)
"
fi

BODY=$(cat <<EOF
Reminder: read this week's Grok journal when you have a minute.

Window (PT): ${START} → ${END}
Entries: ${COUNT}

Folder:
  ${FOLDER_URL}

${LINKS}
That's all — no action required beyond reading if you want.

— zaz-astra grok-journal-read-reminder
EOF
)

if [ "$DRY_RUN" = 1 ]; then
  printf '%s\n' "$BODY"
  exit 0
fi

if [ ! -x "$NOTIFY" ] && [ ! -f "$NOTIFY" ]; then
  echo "grok-journal-read-reminder: missing notify-email at $NOTIFY" >&2
  exit 0
fi

printf '%s\n' "$BODY" | bash "$NOTIFY" "Grok journal — last week ready to read (${COUNT} entries)"
echo "grok-journal-read-reminder: emailed count=$COUNT window=$START..$END"
exit 0
