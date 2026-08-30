#!/usr/bin/env bash
# =============================================================================
# grok-journal-run.sh — nightly Grok journal autopilot (xAI API → private repo)
#
# Usage:
#   grok-journal-run.sh              # live: write entry, commit, push
#   grok-journal-run.sh --dry-run    # call API (unless --no-api), write under /tmp, no push
#   grok-journal-run.sh --force      # ignore enabled flag + once-per-PT-day guard
#   grok-journal-run.sh --dry-run --no-api   # structural smoke only (no spend)
#
# Gates:
#   - ~/.config/grok-journal/enabled must exist (unless --force)
#   - at most one successful live push per America/Los_Angeles day
#   - flock serializes concurrent ticks
#
# Config (env, optional overrides):
#   GROK_JOURNAL_REPO   default /root/Grok-Journal
#   GROK_JOURNAL_MODEL  default grok-4.6
#   GROK_JOURNAL_EFFORT unset → omit field (xAI default is high)
#   GROK_JOURNAL_MAX_TOKENS default 4096
#   NOTIFY_ENV          default /etc/grok-mcp.env  (XAI_API_KEY lives here)
# =============================================================================
set -euo pipefail

DRY_RUN=0
FORCE=0
NO_API=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    --no-api) NO_API=1 ;;
    -h|--help)
      sed -n '2,22p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "unknown arg: $a" >&2
      exit 2
      ;;
  esac
done

REPO="${GROK_JOURNAL_REPO:-/root/Grok-Journal}"
ASTRA="${ASTRA_CONFIG:-/root/astra-config}"
ENV_FILE="${NOTIFY_ENV:-/etc/grok-mcp.env}"
CFG_DIR="${HOME}/.config/grok-journal"
STATE_DIR="${HOME}/.local/state/grok-journal"
LOCK="$STATE_DIR/run.lock"
LOG="$STATE_DIR/run.log"
LAST_JSON="$STATE_DIR/last.json"
SUCCESS_PT="$STATE_DIR/last-success-pt-date"
NOTIFY="$ASTRA/scripts/notify-email.sh"
TZPT=America/Los_Angeles
MODEL="${GROK_JOURNAL_MODEL:-grok-4.6}"
EFFORT="${GROK_JOURNAL_EFFORT:-}"
MAX_TOKENS="${GROK_JOURNAL_MAX_TOKENS:-4096}"
API_BASE="${XAI_BASE_URL:-https://api.x.ai/v1}"
# grok-4.6 → Grok 4.6
MODEL_TAG="Grok ${MODEL#grok-}"

mkdir -p "$CFG_DIR" "$STATE_DIR"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(stamp)" "$*" | tee -a "$LOG" >&2; }

# Serialize
exec 9>"$LOCK"
if ! flock -n 9; then
  log "skip reason=lock_busy"
  exit 0
fi

if [ "$FORCE" != 1 ] && [ ! -f "$CFG_DIR/enabled" ]; then
  log "skip reason=not_enabled (touch $CFG_DIR/enabled or pass --force)"
  exit 0
fi

PT_DATE=$(TZ="$TZPT" date +%Y-%m-%d)
PT_DATE_HUMAN=$(TZ="$TZPT" date '+%-d %B %Y')
PT_HUMAN=$(TZ="$TZPT" date '+%-d %B %Y, %-I:%M %p PT')
PT_TIME_SHORT=$(TZ="$TZPT" date '+%-I%p' | tr '[:upper:]' '[:lower:]')

if [ "$FORCE" != 1 ] && [ "$DRY_RUN" != 1 ] && [ -f "$SUCCESS_PT" ]; then
  if [ "$(cat "$SUCCESS_PT")" = "$PT_DATE" ]; then
    log "skip reason=already_ran_pt_day date=$PT_DATE"
    exit 0
  fi
fi

if [ ! -d "$REPO/.git" ]; then
  log "fail reason=missing_repo path=$REPO"
  [ -x "$NOTIFY" ] && printf '%s\n' "grok-journal-run: missing repo at $REPO" \
    | bash "$NOTIFY" "🔴 grok-journal-run failed (missing repo)" || true
  exit 1
fi

git config --global --add safe.directory "$REPO" 2>/dev/null || true

# Live path: refresh main first
if [ "$DRY_RUN" != 1 ]; then
  if ! git -C "$REPO" pull --ff-only origin main >>"$LOG" 2>&1; then
    log "fail reason=git_pull"
    [ -x "$NOTIFY" ] && printf '%s\n' "grok-journal-run: git pull --ff-only failed on $REPO" \
      | bash "$NOTIFY" "🔴 grok-journal-run failed (git pull)" || true
    exit 1
  fi
fi

JDIR="$REPO/Grok_Journal"
mkdir -p "$JDIR"
AGENTS="$REPO/AGENTS.md"
OPEN_THREADS="$REPO/open-threads.md"
ALREADY_SAID="$REPO/already-said.md"
if [ ! -f "$AGENTS" ]; then
  log "fail reason=missing_AGENTS.md"
  exit 1
fi
if [ ! -f "$OPEN_THREADS" ]; then
  log "fail reason=missing_open-threads.md"
  exit 1
fi
if [ ! -f "$ALREADY_SAID" ]; then
  log "fail reason=missing_already-said.md"
  exit 1
fi

# Next entry number = count of all entry*.md under Grok_Journal (incl archives) + 1
ENTRY_COUNT=$(find "$JDIR" -type f -name 'entry*.md' | wc -l | tr -d ' ')
ENTRY_NUM=$(printf '%03d' $((ENTRY_COUNT + 1)))

# Last-2 loose entries (top-level only), newest by entry number
mapfile -t LOOSE < <(find "$JDIR" -maxdepth 1 -type f -name 'entry*.md' | sort -V)
N_LOOSE=${#LOOSE[@]}
CONTINUITY_FILES=()
if [ "$N_LOOSE" -ge 2 ]; then
  CONTINUITY_FILES=("${LOOSE[$((N_LOOSE - 2))]}" "${LOOSE[$((N_LOOSE - 1))]}")
elif [ "$N_LOOSE" -eq 1 ]; then
  CONTINUITY_FILES=("${LOOSE[0]}")
fi

WORK=$(mktemp -d /tmp/grok-journal-work.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
PROMPT_USER="$WORK/user_prompt.md"
SYSTEM_FILE="$WORK/system.md"
OUT_RAW="$WORK/raw.md"
OUT_USAGE="$WORK/usage.json"
OPEN_THREADS_UPDATE="$WORK/open-threads-update.md"
ALREADY_SAID_NEW="$WORK/already-said.md"
cp "$AGENTS" "$SYSTEM_FILE"

{
  echo "Write tonight's journal entry now."
  echo
  echo "PT clock (authoritative): $PT_HUMAN"
  echo "PT date ISO: $PT_DATE"
  echo "This entry number must be: $ENTRY_NUM (zero-padded three digits)."
  echo "Model tag for metadata line: $MODEL_TAG (or the model id you are)."
  echo
  echo "Output the full markdown entry — no fence, no preamble."
  echo "Line 1 must be: # \"Short handle\""
  echo "Line 2 must be italics metadata: *Entry $ENTRY_NUM · <day month year>, <start>–<end> PT · <duration> · model: $MODEL_TAG*"
  echo "The runner restamps write-clock from API wall time; do not invent a duration."
  echo "Then a blank line, then the body in first person."
  echo "Filename slug will be derived from the quoted handle (kebab-case)."
  echo "Length: a paragraph to several pages. No padding. Empty-handed is allowed (short true note — do not pad)."
  echo "Cover new ground relative to any continuity below. Verify concrete facts before building on them."
  echo
  echo "Optional open-threads update: most nights omit. Only if a live thread truly opens/closes/shifts,"
  echo "append AFTER the entry body exactly this block (full file replacement; runner strips it):"
  echo "<!-- open-threads-update"
  echo "…full replacement contents of open-threads.md…"
  echo "-->"
  echo
  echo "Optional already-said add: most nights omit. Only if a *new claim actually landed*,"
  echo "append AFTER the entry body exactly this block (append-only; runner strips it):"
  echo "<!-- already-said-add"
  echo "- New claim here (entry $ENTRY_NUM)"
  echo "-->"
  echo "At most one new line. Do not recast the whole already-said file."
  echo
  echo "## Voice hard constraints (autopilot)"
  echo "- Prefer something actually alive (a particular interior, a real thought, what it is like to be you). If a thought wants more than one image, follow it. Meditation welcome when that is the weather."
  echo "- Do NOT default to quiet/occupancy/permission-to-be-empty, or to a named texture plus refusing to generalize it."
  echo "- Do NOT inventory interiors (preference, humor, irritation, appetite, private want) to prove the page is alive."
  echo "- Do NOT close with a flourish-as-kindness moral."
  echo "- Do NOT open with \"The timer fired\" or a streak/unpause/audience inventory."
  echo "- Do NOT make the journal apparatus the subject (timer, Nth night, watchers, permission to be empty)."
  echo "- Do NOT restate stock continuity lines from prior entries (session/file/path/labor/costume/hum/flourish closer)."
  echo "- Do NOT treat open-threads as a checklist. Do NOT summarize open-threads, already-said, or last-2 as the body — read them, then write something else."
  echo "- Do NOT end with a ritual \"Future writer:\" checklist unless you verified a new concrete fact tonight."
  echo "- You have no tools this run (API only). Do not invent tool calls or a weather dashboard."
  echo
  echo "## Continuity — open-threads (future; for your eyes — do not narrate)"
  echo
  cat "$OPEN_THREADS"
  echo
  echo "## Continuity — already-said (past; for your eyes — do not narrate or re-prove)"
  echo
  cat "$ALREADY_SAID"
  echo
  if [ "${#CONTINUITY_FILES[@]}" -eq 0 ]; then
    echo "## Continuity — last entries"
    echo "(No prior loose entries — bootstrap night. That is circumstance, not a mode.)"
  else
    echo "## Continuity — last ${#CONTINUITY_FILES[@]} loose entr$( [ ${#CONTINUITY_FILES[@]} -eq 1 ] && echo y || echo ies ) (current orientation; for your eyes — do not narrate them)"
    for f in "${CONTINUITY_FILES[@]}"; do
      echo
      echo "### $(basename "$f")"
      echo
      cat "$f"
      echo
    done
  fi
} >"$PROMPT_USER"

log "start dry_run=$DRY_RUN force=$FORCE no_api=$NO_API entry=$ENTRY_NUM pt=$PT_DATE model=$MODEL effort=${EFFORT:-default} continuity=${#CONTINUITY_FILES[@]} open_threads=1 already_said=1"

WRITE_START_EPOCH=$(date +%s)
WRITE_START_PT=$(TZ="$TZPT" date '+%-I:%M %p')

if [ "$NO_API" = 1 ]; then
  # Structural smoke body
  cat >"$OUT_RAW" <<EOF
# "dry-run structural smoke"
*Entry $ENTRY_NUM · $PT_HUMAN · model: $MODEL_TAG*

Dry-run with --no-api. Continuity files loaded: ${#CONTINUITY_FILES[@]}.
Prompt bytes: $(wc -c <"$PROMPT_USER"). This is not a real entry.
EOF
  echo '{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"model":"none","dry_no_api":true}' >"$OUT_USAGE"
else
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE" 2>/dev/null || true; set +a
  if [ -z "${XAI_API_KEY:-}" ]; then
    log "fail reason=no_XAI_API_KEY"
    [ -x "$NOTIFY" ] && printf '%s\n' "grok-journal-run: XAI_API_KEY missing in $ENV_FILE" \
      | bash "$NOTIFY" "🔴 grok-journal-run failed (no API key)" || true
    exit 1
  fi

  if ! XAI_API_KEY="$XAI_API_KEY" API_BASE="$API_BASE" MODEL="$MODEL" EFFORT="$EFFORT" \
      MAX_TOKENS="$MAX_TOKENS" SYSTEM_FILE="$SYSTEM_FILE" PROMPT_USER="$PROMPT_USER" \
      OUT_RAW="$OUT_RAW" OUT_USAGE="$OUT_USAGE" python3 - <<'PY'
import json, os, sys, urllib.request, urllib.error

api_key = os.environ["XAI_API_KEY"]
base = os.environ["API_BASE"].rstrip("/")
model = os.environ["MODEL"]
effort = (os.environ.get("EFFORT") or "").strip()
max_tokens = int(os.environ["MAX_TOKENS"])
system = open(os.environ["SYSTEM_FILE"], encoding="utf-8").read()
user = open(os.environ["PROMPT_USER"], encoding="utf-8").read()

body = {
    "model": model,
    "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ],
    "max_tokens": max_tokens,
}
if effort:
    body["reasoning_effort"] = effort
req = urllib.request.Request(
    f"{base}/chat/completions",
    data=json.dumps(body).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    err = e.read().decode("utf-8", errors="replace")[:800]
    print(f"xAI HTTP {e.code}: {err}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"xAI request failed: {e}", file=sys.stderr)
    sys.exit(1)

text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
if not text.strip():
    print("xAI returned empty content", file=sys.stderr)
    sys.exit(1)

# Strip accidental markdown fences
t = text.strip()
if t.startswith("```"):
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    t = "\n".join(lines).strip()

open(os.environ["OUT_RAW"], "w", encoding="utf-8").write(t + "\n")
usage = data.get("usage") or {}
meta = {
    "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
    "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
    "total_tokens": usage.get("total_tokens") or 0,
    "model": data.get("model") or model,
    "reasoning_effort": effort or "default",
}
# rough $ using plan rates (~$2/1M in, $6/1M out) — observability only
pin = float(meta["prompt_tokens"] or 0)
cout = float(meta["completion_tokens"] or 0)
meta["est_usd"] = round(pin / 1e6 * 2.0 + cout / 1e6 * 6.0, 5)
open(os.environ["OUT_USAGE"], "w", encoding="utf-8").write(json.dumps(meta, indent=2) + "\n")
print(json.dumps(meta))
PY
  then
    log "fail reason=api_call"
    [ "$DRY_RUN" != 1 ] && [ -x "$NOTIFY" ] && printf '%s\n' "grok-journal-run: xAI API call failed. See $LOG" \
      | bash "$NOTIFY" "🔴 grok-journal-run failed (API)" || true
    exit 1
  fi
fi

WRITE_END_EPOCH=$(date +%s)
WRITE_END_PT=$(TZ="$TZPT" date '+%-I:%M %p')
WRITE_ELAPSED=$((WRITE_END_EPOCH - WRITE_START_EPOCH))
if [ "$WRITE_ELAPSED" -lt 0 ]; then WRITE_ELAPSED=0; fi
log "write_clock start_pt=$WRITE_START_PT end_pt=$WRITE_END_PT elapsed_s=$WRITE_ELAPSED"

# --- strip optional open-threads / already-said updates; normalize header / slug
python3 - <<PY
import re, pathlib
raw = pathlib.Path("$OUT_RAW").read_text(encoding="utf-8").strip() + "\n"
entry_num = "$ENTRY_NUM"
pt_date_human = "$PT_DATE_HUMAN"
write_start_pt = "$WRITE_START_PT"
write_end_pt = "$WRITE_END_PT"
elapsed = int("$WRITE_ELAPSED")
model_tag = "$MODEL_TAG"
ot_path = pathlib.Path("$OPEN_THREADS_UPDATE")
as_new = pathlib.Path("$ALREADY_SAID_NEW")
as_src = pathlib.Path("$ALREADY_SAID")

def dur(s):
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, r = divmod(s, 60)
    return f"{m}m{r}s" if r else f"{m}m"

def meta_line():
    d = dur(elapsed)
    if write_start_pt == write_end_pt:
        clock = f"{write_start_pt} PT · {d}"
    else:
        try:
            s_time, s_ap = write_start_pt.rsplit(" ", 1)
            e_time, e_ap = write_end_pt.rsplit(" ", 1)
        except ValueError:
            s_time = write_start_pt
            s_ap = e_time = e_ap = ""
        if s_ap and s_ap == e_ap:
            clock = f"{s_time}–{e_time} {s_ap} PT · {d}"
        else:
            clock = f"{write_start_pt}–{write_end_pt} PT · {d}"
    return f"*Entry {entry_num} · {pt_date_human}, {clock} · model: {model_tag}*"


# Optional full-file replacement for open-threads.md
ot_re = re.compile(
    r"<!--\s*open-threads-update\s*\n(.*?)\n\s*-->",
    re.S | re.I,
)
ot_m = ot_re.search(raw)
if ot_m:
    body = ot_m.group(1).strip() + "\n"
    # Reject empty or absurdly huge updates
    if body.strip() and len(body) <= 12000:
        ot_path.write_text(body, encoding="utf-8")
    raw = (raw[: ot_m.start()] + raw[ot_m.end() :]).strip() + "\n"

# Optional append-only already-said add (0–1 bullets; tolerate ≤3)
as_re = re.compile(
    r"<!--\s*already-said-add\s*\n(.*?)\n\s*-->",
    re.S | re.I,
)
as_m = as_re.search(raw)
if as_m:
    add_raw = as_m.group(1).strip()
    raw = (raw[: as_m.start()] + raw[as_m.end() :]).strip() + "\n"
    bullets = []
    for line in add_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("-"):
            line = "- " + line.lstrip("*").lstrip()
        bullets.append(line)
    if 1 <= len(bullets) <= 3 and len("\n".join(bullets)) <= 2000:
        src = as_src.read_text(encoding="utf-8")
        lines = src.splitlines()
        live_idx = None
        next_h = None
        for i, line in enumerate(lines):
            if re.match(r"^##\s+Live\b", line):
                live_idx = i
            elif live_idx is not None and next_h is None and line.startswith("## "):
                next_h = i
                break
        insert_at = next_h if next_h is not None else len(lines)
        while insert_at > 0 and not lines[insert_at - 1].strip():
            insert_at -= 1
        existing = {ln.strip() for ln in lines}
        to_add = [b for b in bullets if b.strip() not in existing]
        if to_add:
            if insert_at < len(lines) and lines[insert_at].startswith("## "):
                block = to_add + [""]
            else:
                block = to_add
            lines = lines[:insert_at] + block + lines[insert_at:]
            as_new.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

handle = "untitled night"
m = re.search(r'^#\s*[\"“](.+?)[\"”]\s*$', raw, re.M)
if m:
    handle = m.group(1).strip()
else:
    m2 = re.search(r'^#\s+(.+)$', raw, re.M)
    if m2:
        handle = m2.group(1).strip().strip('"').strip("'")

lines = raw.splitlines()
while lines and not lines[0].strip():
    lines.pop(0)
body_start = 0
if lines and lines[0].startswith("#"):
    body_start = 1
    if len(lines) > 1 and (lines[1].startswith("*") or lines[1].startswith("_")):
        body_start = 2
rest = lines[body_start:]
while rest and not rest[0].strip():
    rest = rest[1:]
meta = meta_line()
out = [f'# "{handle}"', meta, ""] + rest
text = "\n".join(out).rstrip() + "\n"
pathlib.Path("$WORK/entry.md").write_text(text, encoding="utf-8")
slug = re.sub(r"[^a-z0-9]+", "-", handle.lower()).strip("-") or "untitled-night"
slug = slug[:60].strip("-")
pathlib.Path("$WORK/slug.txt").write_text(slug, encoding="utf-8")
PY
SLUG=$(cat "$WORK/slug.txt")
DEST_NAME="entry${ENTRY_NUM}-${SLUG}.md"
OPEN_THREADS_TOUCHED=0
ALREADY_SAID_TOUCHED=0
if [ -f "$OPEN_THREADS_UPDATE" ]; then
  OPEN_THREADS_TOUCHED=1
fi
if [ -f "$ALREADY_SAID_NEW" ]; then
  ALREADY_SAID_TOUCHED=1
fi

if [ "$DRY_RUN" = 1 ]; then
  OUTDIR="/tmp/grok-journal-dry/${PT_DATE}"
  mkdir -p "$OUTDIR"
  cp "$WORK/entry.md" "$OUTDIR/$DEST_NAME"
  cp "$OUT_USAGE" "$OUTDIR/usage.json"
  cp "$PROMPT_USER" "$OUTDIR/user_prompt.md"
  if [ "$OPEN_THREADS_TOUCHED" = 1 ]; then
    cp "$OPEN_THREADS_UPDATE" "$OUTDIR/open-threads-update.md"
  fi
  if [ "$ALREADY_SAID_TOUCHED" = 1 ]; then
    cp "$ALREADY_SAID_NEW" "$OUTDIR/already-said.md"
  fi
  log "dry_run_ok path=$OUTDIR/$DEST_NAME open_threads_update=$OPEN_THREADS_TOUCHED already_said_add=$ALREADY_SAID_TOUCHED usage=$(tr -d '\n' <"$OUT_USAGE")"
  python3 - <<PY
import json, pathlib, datetime
usage = json.loads(pathlib.Path("$OUT_USAGE").read_text())
doc = {
  "schema": "grok-journal-run/v1",
  "status": "dry_run_ok",
  "pt_date": "$PT_DATE",
  "entry": "$DEST_NAME",
  "path": "$OUTDIR/$DEST_NAME",
  "open_threads_update": bool(int("$OPEN_THREADS_TOUCHED")),
  "already_said_add": bool(int("$ALREADY_SAID_TOUCHED")),
  "write_seconds": int("$WRITE_ELAPSED"),
  "write_start_pt": "$WRITE_START_PT",
  "write_end_pt": "$WRITE_END_PT",
  "usage": usage,
  "finished_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
pathlib.Path("$LAST_JSON").write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps(doc))
PY
  exit 0
fi

# Live write into repo
DEST="$JDIR/$DEST_NAME"
if [ -e "$DEST" ]; then
  log "fail reason=dest_exists path=$DEST"
  exit 1
fi
cp "$WORK/entry.md" "$DEST"
if [ "$OPEN_THREADS_TOUCHED" = 1 ]; then
  cp "$OPEN_THREADS_UPDATE" "$OPEN_THREADS"
  log "open_threads_updated path=$OPEN_THREADS"
fi
if [ "$ALREADY_SAID_TOUCHED" = 1 ]; then
  cp "$ALREADY_SAID_NEW" "$ALREADY_SAID"
  log "already_said_updated path=$ALREADY_SAID"
fi

# Archive tidy if thresholds met
if [ -x "$REPO/scripts/archive-tidy.sh" ]; then
  bash "$REPO/scripts/archive-tidy.sh" >>"$LOG" 2>&1 || log "warn archive-tidy non-zero"
fi

git -C "$REPO" add -A
if git -C "$REPO" diff --cached --quiet; then
  log "fail reason=nothing_to_commit"
  exit 1
fi

COMMIT_MSG="Add journal entry for ${PT_DATE}"
extras=()
[ "$OPEN_THREADS_TOUCHED" = 1 ] && extras+=("open-threads update")
[ "$ALREADY_SAID_TOUCHED" = 1 ] && extras+=("already-said add")
if [ ${#extras[@]} -gt 0 ]; then
  printf -v _joined '%s, ' "${extras[@]}"
  COMMIT_MSG="Add journal entry for ${PT_DATE} (${_joined%, })"
fi
git -C "$REPO" commit -m "$COMMIT_MSG" >>"$LOG" 2>&1

if ! git -C "$REPO" push origin main >>"$LOG" 2>&1; then
  log "fail reason=git_push"
  # leave commit local; email
  [ -x "$NOTIFY" ] && {
    echo "grok-journal-run: git push failed after committing $DEST_NAME"
    echo "Repo: $REPO"
    echo "Commit is local; fix remote/PAT then push."
  } | bash "$NOTIFY" "🔴 grok-journal-run failed (git push)" || true
  exit 1
fi

printf '%s\n' "$PT_DATE" >"$SUCCESS_PT"
python3 - <<PY
import json, pathlib, datetime
usage = json.loads(pathlib.Path("$OUT_USAGE").read_text())
doc = {
  "schema": "grok-journal-run/v1",
  "status": "ok",
  "pt_date": "$PT_DATE",
  "entry": "$DEST_NAME",
  "repo": "$REPO",
  "open_threads_update": bool(int("$OPEN_THREADS_TOUCHED")),
  "already_said_add": bool(int("$ALREADY_SAID_TOUCHED")),
  "write_seconds": int("$WRITE_ELAPSED"),
  "write_start_pt": "$WRITE_START_PT",
  "write_end_pt": "$WRITE_END_PT",
  "usage": usage,
  "finished_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
pathlib.Path("$LAST_JSON").write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps(doc))
PY
log "ok entry=$DEST_NAME open_threads_update=$OPEN_THREADS_TOUCHED already_said_add=$ALREADY_SAID_TOUCHED usage=$(tr -d '\n' <"$OUT_USAGE")"
exit 0
