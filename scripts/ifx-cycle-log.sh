#!/usr/bin/env bash
# ifx-cycle-log.sh — private IFX end-of-cycle symptom log (not git / not Hermes).
set -euo pipefail
DIR="${IFX_LOG_DIR:-$HOME/.local/state/health/ifx-cycle}"
JSONL="$DIR/log.jsonl"
CSV="$DIR/log.csv"
CFG="$DIR/config.json"
mkdir -p "$DIR"
chmod 700 "$DIR" 2>/dev/null || true

usage() {
  cat <<U
usage:
  ifx-cycle-log.sh set-infusion YYYY-MM-DD   # last infusion date (PT calendar)
  ifx-cycle-log.sh add [--date YYYY-MM-DD] [--days-since N] \\
       --sleep 1-5 --energy 1-5 --stool 1-7 --cramp none|mild|moderate \\
       [--bm N] [--notes text]
  ifx-cycle-log.sh add          # interactive (days-since auto from last infusion if set)
  ifx-cycle-log.sh list [-n N]
  ifx-cycle-log.sh status       # last infusion + days since + recent rows
  ifx-cycle-log.sh path
U
}

cmd="${1:-}"
shift || true

case "$cmd" in
  path) echo "$DIR"; exit 0 ;;

  set-infusion)
    d="${1:-}"
    if [[ -z "$d" ]]; then
      echo "usage: ifx-cycle-log.sh set-infusion YYYY-MM-DD" >&2
      exit 2
    fi
    python3 - "$CFG" "$d" <<'PY'
import json, sys
from pathlib import Path
from datetime import date, datetime, timezone
cfg_path, d = Path(sys.argv[1]), sys.argv[2]
date.fromisoformat(d)  # validate
data = {}
if cfg_path.is_file():
    try:
        data = json.loads(cfg_path.read_text())
    except Exception:
        data = {}
data["last_infusion_date"] = d
data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
cfg_path.write_text(json.dumps(data, indent=2) + "\n")
cfg_path.chmod(0o600)
print(json.dumps(data, indent=2))
PY
    exit 0
    ;;

  status)
    python3 - "$DIR" <<'PY'
import json, sys
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo
d = Path(sys.argv[1])
cfg = {}
if (d / "config.json").is_file():
    cfg = json.loads((d / "config.json").read_text())
inf = cfg.get("last_infusion_date")
today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
print(f"dir: {d}")
if inf:
    days = (today - date.fromisoformat(inf)).days
    print(f"last_infusion: {inf}  days_since: {days}  (today PT {today})")
else:
    print("last_infusion: (not set — run set-infusion)")
jsonl = d / "log.jsonl"
if not jsonl.is_file():
    print("rows: 0")
    raise SystemExit(0)
lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
print(f"rows: {len(lines)}")
for line in lines[-5:]:
    o = json.loads(line)
    cramp = o.get("cramp") or o.get("gi") or "?"
    stool = o.get("stool_form_1_7")
    stool_s = f"stool={stool}" if stool is not None and stool != "" else "stool=—"
    bm = o.get("bm_count")
    bm_s = f"  bm={bm}" if bm is not None and bm != "" else ""
    print(
        f"  {o.get('date')}  d+{o.get('days_since_infusion')}  "
        f"sleep={o.get('sleep_1_5')}  energy={o.get('energy_floaty_1_5')}  "
        f"{stool_s}  cramp={cramp}{bm_s}  {(o.get('notes') or '')[:50]}"
    )
PY
    exit 0
    ;;

  list)
    n=20
    if [[ "${1:-}" == "-n" ]]; then n="${2:-20}"; fi
    if [[ ! -f "$JSONL" ]]; then echo "(empty)"; exit 0; fi
    python3 - "$JSONL" "$n" <<'PYL'
import json, sys
from pathlib import Path
path, n = Path(sys.argv[1]), int(sys.argv[2])
lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
for line in lines[-n:]:
    o = json.loads(line)
    cramp = o.get("cramp") or o.get("gi") or "?"
    stool = o.get("stool_form_1_7")
    stool_s = f"stool={stool}" if stool is not None and stool != "" else "stool=—"
    bm = o.get("bm_count")
    bm_s = f"  bm={bm}" if bm is not None and bm != "" else ""
    print(
        f"{o.get('date')}  d+{o.get('days_since_infusion')}  "
        f"sleep={o.get('sleep_1_5')}  energy={o.get('energy_floaty_1_5')}  "
        f"{stool_s}  cramp={cramp}{bm_s}  {o.get('notes') or ''}"
    )
PYL
    exit 0
    ;;


  add)
    date=""; days=""; sleep=""; energy=""; stool=""; cramp=""; bm=""; notes=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --date) date="$2"; shift 2 ;;
        --days-since) days="$2"; shift 2 ;;
        --sleep) sleep="$2"; shift 2 ;;
        --energy) energy="$2"; shift 2 ;;
        --stool) stool="$2"; shift 2 ;;
        --cramp|--gi) cramp="$2"; shift 2 ;;  # --gi legacy alias
        --bm) bm="$2"; shift 2 ;;
        --notes) notes="$2"; shift 2 ;;
        *) echo "unknown: $1" >&2; usage; exit 2 ;;
      esac
    done
    if [[ -z "$sleep" || -z "$energy" || -z "$stool" || -z "$cramp" ]]; then
      date="${date:-$(TZ=America/Los_Angeles date +%F)}"
      if [[ -z "$days" && -f "$CFG" ]]; then
        days="$(python3 - "$CFG" <<'PY'
import json, sys
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
inf = cfg.get("last_infusion_date")
if not inf:
    raise SystemExit("")
today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
print((today - date.fromisoformat(inf)).days)
PY
)" || days=""
      fi
      [[ -n "$days" ]] && echo "(auto days_since=$days from last infusion)" || true
      read -r -p "days since last infusion${days:+ [$days]}: " d_in
      days="${d_in:-$days}"
      read -r -p "sleep 1-5: " sleep
      read -r -p "energy/floaty 1-5 (5=solid): " energy
      echo "stool 1-7: 1 deer · 4 smooth gold · 7 taco bell/liquid"
      read -r -p "  stool: " stool
      read -r -p "cramp none/mild/moderate: " cramp
      read -r -p "bm count today (blank=skip): " bm
      read -r -p "notes: " notes
    fi
    # auto days-since if still empty
    if [[ -z "$days" && -f "$CFG" ]]; then
      days="$(python3 - "$CFG" <<'PY'
import json, sys
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text())
inf = cfg.get("last_infusion_date")
if not inf:
    raise SystemExit(2)
today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
print((today - date.fromisoformat(inf)).days)
PY
)"
    fi
    if [[ -z "$days" || -z "$sleep" || -z "$energy" || -z "$stool" || -z "$cramp" ]]; then
      echo "need --days-since (or set-infusion) + --sleep + --energy + --stool + --cramp" >&2
      exit 2
    fi
    date="${date:-$(TZ=America/Los_Angeles date +%F)}"
    python3 - "$JSONL" "$CSV" "$date" "$days" "$sleep" "$energy" "$stool" "$cramp" "$bm" "$notes" <<'PY'
import json, sys, csv
from pathlib import Path
from datetime import datetime, timezone
jsonl, csv_path, d, days, sleep, energy, stool, cramp, bm_raw, notes = sys.argv[1:11]
row = {
  "date": d,
  "days_since_infusion": int(days),
  "sleep_1_5": int(sleep),
  "energy_floaty_1_5": int(energy),
  "stool_form_1_7": int(stool),
  "cramp": cramp.strip().lower(),
  "bm_count": None,
  "notes": notes or "",
  "logged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
  "source": "cli",
}
if bm_raw.strip() != "":
    row["bm_count"] = int(bm_raw)
    if not (0 <= row["bm_count"] <= 30):
        raise SystemExit("bm_count must be 0-30")
if not (1 <= row["sleep_1_5"] <= 5 and 1 <= row["energy_floaty_1_5"] <= 5):
    raise SystemExit("sleep/energy must be 1-5")
if not (1 <= row["stool_form_1_7"] <= 7):
    raise SystemExit("stool form must be 1-7")
if row["cramp"] not in ("none", "mild", "moderate"):
    raise SystemExit("cramp must be none|mild|moderate")

CSV_FIELDS = [
    "date",
    "days_since_infusion",
    "sleep_1_5",
    "energy_floaty_1_5",
    "stool_form_1_7",
    "cramp",
    "bm_count",
    "notes",
]

def normalize(o):
    out = dict(o)
    if "cramp" not in out and out.get("gi"):
        out["cramp"] = out["gi"]
    for k in ("stool_form_1_7", "bm_count"):
        if k not in out:
            out[k] = ""
    return out

Path(jsonl).parent.mkdir(parents=True, exist_ok=True)
with open(jsonl, "a") as f:
    f.write(json.dumps(row, sort_keys=True) + "\n")
Path(jsonl).chmod(0o600)

# rebuild CSV from full jsonl (field migration-safe)
all_rows = []
for ln in Path(jsonl).read_text().splitlines():
    if ln.strip():
        all_rows.append(normalize(json.loads(ln)))
with open(csv_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in all_rows:
        w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
Path(csv_path).chmod(0o600)
print(json.dumps(row, indent=2))
PY
    exit 0
    ;;

  ""|-h|--help) usage; exit 0 ;;
  *) usage; exit 2 ;;
esac
