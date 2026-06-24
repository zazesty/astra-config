#!/usr/bin/env bash
# usage-gate.sh -- decide whether the journal trigger may fire, based on
# current Claude plan usage.
#
#   exit 0  -> gate PASSES (under budget, ok to fire)
#   exit 1  -> gate FAILS  (over pace / over ceiling / any error -> fail closed)
#
# Reads the OAuth access token from ~/.claude/.credentials.json and queries the
# undocumented usage endpoint. NO retries: any non-200 (incl. 429) or unexpected
# response fails CLOSED with a logged warning. The daily-floor path in
# journal-trigger.sh is what guarantees an entry regardless of this gate.
#
# Field mapping (confirmed live against the real response, not guessed):
#   .seven_day.utilization  -> weekly %      (0-100 scale)
#   .five_hour.utilization   -> 5-hour %      (0-100 scale)
#   .seven_day.resets_at     -> NEXT weekly reset (future). Window start is
#                               resets_at - 168h, so hours-into-week =
#                               168 - hours_until(resets_at).
#
# Gate passes iff:  weekly_pct < weekly_target   AND   five_hour_pct < 80
#   weekly_target = 0.5 * hours_into_week              (normal half-rate burn)
#   weekly_target = 95 (flat)  in the last 5h before the weekly reset
#       ("use it or lose it": fill the weekly budget that would otherwise reset
#        unused. The 5-hour ceiling still applies, so the burst can't spike.)
#
# Prints one machine-readable summary line to stdout (consumed/logged by the
# caller). With --raw, also dumps the raw JSON body to stderr.
#
# Usage: usage-gate.sh [--raw]

set -u

CRED="${HOME}/.claude/.credentials.json"
USAGE_URL="https://api.anthropic.com/api/oauth/usage"
LOG="${HOME}/.local/state/journal-cron.log"

FIVE_HOUR_CEIL=80          # 5-hour utilization ceiling (percent)
WEEKLY_SLOPE=0.5           # normal: allowed weekly percent per hour-into-window
WEEK_HOURS=168             # 7-day window length
WEEKLY_FINAL_WINDOW_H=5    # "use it or lose it" window before the weekly reset
WEEKLY_FINAL_CEIL=95       # in that window, lift the weekly allowance to this flat %

RAW=0
[ "${1:-}" = "--raw" ] && RAW=1

mkdir -p "$(dirname "$LOG")"
warn() { printf '%s [usage-gate] WARN %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

# Emit a fail-closed summary line and exit nonzero.
fail_closed() { # $1 = reason
  echo "weekly_pct=NA target_pct=NA mode=NA five_hour_pct=NA hours_into_week=NA hours_until_reset=NA weekly_reset=NA gate_decision=fail gate_reason=$1"
  exit 1
}

# --- token ---------------------------------------------------------------
TOKEN=$(jq -r '.claudeAiOauth.accessToken // empty' "$CRED" 2>/dev/null)
if [ -z "$TOKEN" ]; then warn "no access token in $CRED"; fail_closed no_token; fi

# --- fetch (no retries) --------------------------------------------------
BODY=$(curl -sS -m 20 -w $'\n%{http_code}' \
  -H "Authorization: Bearer $TOKEN" \
  -H "anthropic-beta: oauth-2025-04-20" \
  -H "anthropic-version: 2023-06-01" \
  "$USAGE_URL" 2>>"$LOG")
CURL_RC=$?
HTTP=$(printf '%s' "$BODY" | tail -n1)
JSON=$(printf '%s' "$BODY" | sed '$d')

[ "$RAW" = 1 ] && printf '%s\n' "$JSON" >&2

if [ "$CURL_RC" -ne 0 ]; then warn "curl failed rc=$CURL_RC"; fail_closed "curl_rc_${CURL_RC}"; fi
if [ "$HTTP" != "200" ]; then warn "HTTP $HTTP from usage endpoint"; fail_closed "http_${HTTP}"; fi

# --- parse ---------------------------------------------------------------
IFS=$'\t' read -r weekly_pct five_pct weekly_reset <<EOF
$(printf '%s' "$JSON" | jq -r '[.seven_day.utilization, .five_hour.utilization, .seven_day.resets_at] | @tsv' 2>/dev/null)
EOF

if [ -z "${weekly_pct:-}" ] || [ "$weekly_pct" = "null" ] \
  || [ -z "${five_pct:-}" ] || [ "$five_pct" = "null" ] \
  || [ -z "${weekly_reset:-}" ] || [ "$weekly_reset" = "null" ]; then
  warn "unexpected response shape (missing fields)"; fail_closed bad_shape
fi

# --- compute weekly allowance --------------------------------------------
reset_epoch=$(date -d "$weekly_reset" +%s 2>/dev/null)
if [ -z "$reset_epoch" ]; then warn "could not parse reset ts: $weekly_reset"; fail_closed bad_ts; fi
now_epoch="${USAGE_NOW_EPOCH:-$(date -u +%s)}"   # USAGE_NOW_EPOCH: test-only override

# hours since window start (= reset - 168h), clamped [0,168]; and hours until reset
hours_since=$(awk -v r="$reset_epoch" -v n="$now_epoch" -v wh="$WEEK_HOURS" \
  'BEGIN{ s=r-wh*3600; h=(n-s)/3600; if(h<0)h=0; if(h>wh)h=wh; printf "%.2f", h }')
hours_until=$(awk -v r="$reset_epoch" -v n="$now_epoch" 'BEGIN{ printf "%.2f", (r-n)/3600 }')

# Effective weekly ceiling: normally the half-rate linear burn target. But in the
# final WEEKLY_FINAL_WINDOW_H hours before reset, lift it to a flat WEEKLY_FINAL_CEIL
# so the otherwise-wasted weekly budget fills ("use it or lose it"). The 5-hour
# ceiling still applies, which keeps the end-of-week burst from spiking.
if awk -v hu="$hours_until" -v w="$WEEKLY_FINAL_WINDOW_H" 'BEGIN{ exit !(hu>=0 && hu<=w) }'; then
  mode=final_window
  target=$(awk -v c="$WEEKLY_FINAL_CEIL" 'BEGIN{ printf "%.2f", c }')
else
  mode=normal
  target=$(awk -v sl="$WEEKLY_SLOPE" -v h="$hours_since" 'BEGIN{ printf "%.2f", sl*h }')
fi

# --- decide --------------------------------------------------------------
w_ok=$(awk -v w="$weekly_pct" -v t="$target" 'BEGIN{ print (w<t)?1:0 }')
f_ok=$(awk -v f="$five_pct" -v c="$FIVE_HOUR_CEIL" 'BEGIN{ print (f<c)?1:0 }')

if [ "$w_ok" = 1 ] && [ "$f_ok" = 1 ]; then
  decision=pass; reason=ok
else
  decision=fail
  if [ "$w_ok" = 0 ] && [ "$f_ok" = 0 ]; then reason=weekly_and_five_over
  elif [ "$w_ok" = 0 ]; then reason=$([ "$mode" = final_window ] && echo weekly_over_final_ceil || echo weekly_over_pace)
  else reason=five_hour_ceiling; fi
fi

echo "weekly_pct=$weekly_pct target_pct=$target mode=$mode five_hour_pct=$five_pct hours_into_week=$hours_since hours_until_reset=$hours_until weekly_reset=$weekly_reset gate_decision=$decision gate_reason=$reason"
[ "$decision" = pass ] && exit 0 || exit 1
